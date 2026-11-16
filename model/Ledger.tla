------------------------------- MODULE Ledger -------------------------------
(***************************************************************************)
(* A model of the ledger's DESIGN: transfer, group commit, crash and       *)
(* recovery. It is checked with TLC at small sizes (see MC.tla and the     *)
(* .cfg files; `make model`). It is not the C code and proves nothing      *)
(* about the C code; DESIGN.md section 10 says what it does and does not   *)
(* show, and maps every action below to the C function it stands for.      *)
(*                                                                         *)
(* An LSN here is a count of log records (the C code uses byte offsets).   *)
(* A batch is a sequence of records. A batch on disk without its commit    *)
(* record is not modelled as bytes: recovery drops it whole (decision      *)
(* D26), so "partly written" and "not written" are the same outcome.       *)
(***************************************************************************)
EXTENDS Integers, Sequences, FiniteSets

CONSTANTS Accounts,     \* e.g. {"A", "B"}
          Init,         \* initial balance of each account
          Reqs,         \* the transfer requests clients may send:
                        \* records [id, from, to, amt]
          Threads,
          BufCap,       \* records the commit buffer holds
          MaxOps,       \* operations per thread
          MaxCrashes,
          Bug           \* "none", or the name of a planted bug

VARIABLES
    \* volatile: lost in a crash
    bal,        \* balances in memory
    idem,       \* the idempotency table: set of applied requests
    buf,        \* log buffer: records appended, not yet handed to the disk
    inflight,   \* the batch a leader is writing (<<>> if none)
    written,    \* the batch reached the page cache / disk, not yet synced
    pc,         \* per thread: "idle", "locked", "wait"
    cur,        \* per thread: the request (or read) in progress
    res,        \* per thread: the answer it will give
    lsn,        \* per thread: what must be durable before it answers
    \* persistent
    disk,       \* sequence of complete, synced batches
    \* bookkeeping of the model, not of the ledger
    up, ops, crashes,
    acks        \* every answer a client ever received

vars == <<bal, idem, buf, inflight, written, pc, cur, res, lsn, disk, up,
          ops, crashes, acks>>

RECURSIVE Flat(_)
Flat(bs) == IF bs = <<>> THEN <<>> ELSE Head(bs) \o Flat(Tail(bs))

DiskRecs == Flat(disk)                      \* the durable log
AllRecs  == DiskRecs \o inflight \o buf     \* the whole log, in LSN order
LogEnd   == Len(AllRecs)
Durable  == Len(DiskRecs)

\* Balance of account a after the first n records of recs.
RECURSIVE BalAfter(_, _, _)
BalAfter(a, recs, n) ==
    IF n = 0 THEN Init[a]
    ELSE LET r == recs[n]
             p == BalAfter(a, recs, n - 1)
         IN  IF r.from = a THEN p - r.amt
             ELSE IF r.to = a THEN p + r.amt ELSE p

Replay(recs) == [a \in Accounts |-> BalAfter(a, recs, Len(recs))]
Range(recs)  == {recs[i] : i \in 1..Len(recs)}

RECURSIVE Sum(_, _)
Sum(f, S) == IF S = {} THEN 0
             ELSE LET a == CHOOSE x \in S : TRUE IN f[a] + Sum(f, S \ {a})

NoReq == [id |-> 0, from |-> "", to |-> "", amt |-> 0]

Start ==
    /\ bal = Init /\ idem = {} /\ buf = <<>> /\ inflight = <<>>
    /\ written = FALSE /\ disk = <<>> /\ up = TRUE /\ crashes = 0
    /\ pc = [t \in Threads |-> "idle"] /\ cur = [t \in Threads |-> NoReq]
    /\ res = [t \in Threads |-> "-"] /\ lsn = [t \in Threads |-> 0]
    /\ ops = [t \in Threads |-> 0] /\ acks = {}

\* The locks a thread in state "locked" holds: its id and both accounts.
Conflict(t, id, accs) ==
    \E u \in Threads \ {t} :
        /\ pc[u] = "locked"
        /\ \/ cur[u].id = id /\ id # 0
           \/ {cur[u].from, cur[u].to} \cap accs # {}

(***************************************************************************)
(* Begin: lg_transfer takes the id's shard lock and both account locks.    *)
(***************************************************************************)
Begin(t, r) ==
    /\ up /\ pc[t] = "idle" /\ ops[t] < MaxOps
    /\ ~Conflict(t, r.id, {r.from, r.to})
    /\ pc' = [pc EXCEPT ![t] = "locked"]
    /\ cur' = [cur EXCEPT ![t] = r]
    /\ ops' = [ops EXCEPT ![t] = @ + 1]
    /\ UNCHANGED <<bal, idem, buf, inflight, written, res, lsn, disk, up,
                   crashes, acks>>

(***************************************************************************)
(* Apply: under the locks, decide; if OK, append the record to the log     *)
(* buffer, change both balances, remember the id; release the locks.       *)
(* A full buffer blocks the OK case here, WITH the locks held (gap 1).     *)
(* An answer derived from state notes the log end it has to wait for.      *)
(***************************************************************************)
Apply(t) ==
    LET r    == cur[t]
        old  == {o \in idem : o.id = r.id}
    IN
    /\ up /\ pc[t] = "locked"
    /\ \/ /\ old # {}
          /\ res' = [res EXCEPT ![t] = IF r \in old THEN "DUPLICATE"
                                                    ELSE "INVALID"]
          /\ lsn' = [lsn EXCEPT ![t] = LogEnd]
          /\ UNCHANGED <<bal, idem, buf>>
       \/ /\ old = {} /\ bal[r.from] < r.amt
          /\ res' = [res EXCEPT ![t] = "INSUFFICIENT"]
          /\ lsn' = [lsn EXCEPT ![t] = LogEnd]
          /\ UNCHANGED <<bal, idem, buf>>
       \/ /\ old = {} /\ bal[r.from] >= r.amt
          /\ Len(buf) < BufCap
          /\ buf' = Append(buf, r)
          /\ bal' = [bal EXCEPT ![r.from] = @ - r.amt, ![r.to] = @ + r.amt]
          /\ idem' = idem \cup {r}
          /\ res' = [res EXCEPT ![t] = "OK"]
          /\ lsn' = [lsn EXCEPT ![t] = LogEnd + 1]
    /\ pc' = [pc EXCEPT ![t] = "wait"]
    /\ UNCHANGED <<inflight, written, cur, disk, up, ops, crashes, acks>>

(***************************************************************************)
(* Read: lg_balance. Takes the account lock, copies the balance, notes     *)
(* the log end. The value read travels in the amt field.                   *)
(***************************************************************************)
Read(t, a) ==
    /\ up /\ pc[t] = "idle" /\ ops[t] < MaxOps
    /\ ~Conflict(t, 0, {a})
    /\ cur' = [cur EXCEPT ![t] = [id |-> 0, from |-> a, to |-> a,
                                  amt |-> bal[a]]]
    /\ res' = [res EXCEPT ![t] = "BALANCE"]
    /\ lsn' = [lsn EXCEPT ![t] = LogEnd]
    /\ pc' = [pc EXCEPT ![t] = "wait"]
    /\ ops' = [ops EXCEPT ![t] = @ + 1]
    /\ UNCHANGED <<bal, idem, buf, inflight, written, disk, up, crashes,
                   acks>>

(***************************************************************************)
(* Return: lg_settle. The caller gets its answer only once the log is      *)
(* durable up to the LSN it noted. The planted bugs skip that wait for     *)
(* one kind of answer each.                                                *)
(***************************************************************************)
Early(t) ==
    \/ Bug = "AckBeforeFlush" /\ res[t] \in {"OK", "DUPLICATE"}
    \/ Bug = "ReadBeforeDurable" /\ res[t] = "BALANCE"
    \/ Bug = "RejectBeforeDurable" /\ res[t] \in {"INSUFFICIENT", "INVALID"}

Return(t) ==
    /\ up /\ pc[t] = "wait"
    /\ \/ lsn[t] <= Durable
       \/ Early(t)
    /\ acks' = acks \cup {[req |-> cur[t], res |-> res[t], lsn |-> lsn[t]]}
    /\ pc' = [pc EXCEPT ![t] = "idle"]
    /\ UNCHANGED <<bal, idem, buf, inflight, written, cur, res, lsn, disk,
                   up, ops, crashes>>

(***************************************************************************)
(* Group commit, in three steps. Which thread leads is not modelled.       *)
(***************************************************************************)
FlushStart ==       \* the leader takes the whole buffer, swaps in an empty one
    /\ up /\ inflight = <<>> /\ buf # <<>>
    /\ inflight' = buf /\ buf' = <<>> /\ written' = FALSE
    /\ UNCHANGED <<bal, idem, pc, cur, res, lsn, disk, up, ops, crashes,
                   acks>>

FlushWrite ==       \* write(): the batch is in the page cache
    /\ up /\ inflight # <<>> /\ ~written
    /\ written' = TRUE
    /\ UNCHANGED <<bal, idem, buf, inflight, pc, cur, res, lsn, disk, up,
                   ops, crashes, acks>>

FlushSync ==        \* fdatasync() returned: durable, waiters are woken
    /\ up /\ inflight # <<>> /\ written
    /\ disk' = Append(disk, inflight)
    /\ inflight' = <<>> /\ written' = FALSE
    /\ UNCHANGED <<bal, idem, buf, pc, cur, res, lsn, up, ops, crashes,
                   acks>>

(***************************************************************************)
(* Crash: everything volatile is gone, calls in progress never return.     *)
(* A batch that was written but not synced may or may not survive whole    *)
(* (kill -9: it does; power loss: either). Synced batches always survive.  *)
(***************************************************************************)
Crash ==
    /\ up /\ crashes < MaxCrashes
    /\ disk' \in IF inflight # <<>> /\ written
                 THEN {disk, Append(disk, inflight)} ELSE {disk}
    /\ up' = FALSE /\ crashes' = crashes + 1
    /\ bal' = Init /\ idem' = {} /\ buf' = <<>> /\ inflight' = <<>>
    /\ written' = FALSE
    /\ pc' = [t \in Threads |-> "idle"] /\ cur' = [t \in Threads |-> NoReq]
    /\ res' = [t \in Threads |-> "-"] /\ lsn' = [t \in Threads |-> 0]
    /\ UNCHANGED <<ops, acks>>

(***************************************************************************)
(* Recover: memory is rebuilt from the complete batches on disk, by        *)
(* replaying them. A function of the disk alone, so I6 (determinism)       *)
(* holds in this model by construction; it is not a checked property.      *)
(***************************************************************************)
Recover ==
    /\ ~up
    /\ bal' = Replay(DiskRecs) /\ idem' = Range(DiskRecs)
    /\ up' = TRUE
    /\ UNCHANGED <<buf, inflight, written, pc, cur, res, lsn, disk, ops,
                   crashes, acks>>

\* Nothing left to do: keeps TLC's deadlock check meaningful everywhere else.
Done ==
    /\ up /\ crashes = MaxCrashes /\ buf = <<>> /\ inflight = <<>>
    /\ \A t \in Threads : pc[t] = "idle" /\ ops[t] = MaxOps
    /\ UNCHANGED vars

Next ==
    \/ \E t \in Threads : \/ \E r \in Reqs : Begin(t, r)
                          \/ \E a \in Accounts : Read(t, a)
                          \/ Apply(t) \/ Return(t)
    \/ FlushStart \/ FlushWrite \/ FlushSync \/ Crash \/ Recover \/ Done

Spec == Start /\ [][Next]_vars

-----------------------------------------------------------------------------
(* The invariants of the brief. *)

\* I1 conservation
I1 == up => Sum(bal, Accounts) = Sum(Init, Accounts)

\* I2 no negative balance: in memory, and after every prefix of the log
\* (the second part is "no transfer survives without the one that funded it")
I2 == /\ up => \A a \in Accounts : bal[a] >= 0
      /\ \A a \in Accounts : \A n \in 0..LogEnd : BalAfter(a, AllRecs, n) >= 0

\* I3 exactly-once: no id twice in the log
I3 == \A i, j \in 1..LogEnd : i # j => AllRecs[i].id # AllRecs[j].id

\* I4 atomicity: memory is exactly the log, every transfer taken whole
I4 == up => /\ bal = Replay(AllRecs)
            /\ idem = Range(AllRecs)

\* I5 durability, as a client sees it: after a crash, everything that was
\* answered OK or DUPLICATE is on disk.
I5 == ~up => \A k \in acks : k.res \in {"OK", "DUPLICATE"}
                                 => k.req \in Range(DiskRecs)

\* The same, at every moment (stronger: holds before any crash happens).
AckedIsDurable == \A k \in acks : k.res \in {"OK", "DUPLICATE"}
                                      => k.req \in Range(DiskRecs)

\* Gap 3: whatever a client was shown is explained by the durable log up
\* to the log end at the moment it looked.
Observed ==
    \A k \in acks :
        /\ k.res \in {"BALANCE", "INSUFFICIENT", "INVALID"} => k.lsn <= Durable
        /\ k.res = "BALANCE" =>
               BalAfter(k.req.from, DiskRecs, k.lsn) = k.req.amt
        /\ k.res = "INSUFFICIENT" =>
               BalAfter(k.req.from, DiskRecs, k.lsn) < k.req.amt
        /\ k.res = "INVALID" =>
               \E i \in 1..k.lsn : /\ DiskRecs[i].id = k.req.id
                                   /\ DiskRecs[i] # k.req

\* The same as a client sees it: checked once the machine has crashed.
ObservedAfterCrash == ~up => Observed

\* Group commit keeps the log a strict sequence: one batch in flight at most,
\* never more records in a batch than the buffer holds.
Batches == /\ Len(buf) <= BufCap /\ Len(inflight) <= BufCap
           /\ \A i \in 1..Len(disk) : Len(disk[i]) \in 1..BufCap

\* Not invariants: probes. TLC must report each as VIOLATED, which shows
\* that the situation is reachable in the model. Without such a check,
\* green invariants could be vacuous.
\* 1. After a crash and recovery, with an OK already handed out: a thread
\*    holds its locks in front of a full buffer.
ProbeBlocked == ~ ( /\ up /\ crashes = 1 /\ Len(buf) = BufCap
                    /\ \E t \in Threads : pc[t] = "locked"
                    /\ \E k \in acks : k.res = "OK" )
\* 2. The same while another batch is in flight (the gap-1 situation of
\*    tests/gate.c). Needs three threads; unreachable with two.
ProbeInFlight == ~ ( /\ up /\ Len(buf) = BufCap /\ inflight # <<>>
                     /\ \E t \in Threads : pc[t] = "locked" )
=============================================================================
