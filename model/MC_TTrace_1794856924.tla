---- MODULE MC_TTrace_1794856924 ----
EXTENDS Sequences, TLCExt, MC, Toolbox, Naturals, TLC, MC_TEConstants

_expression ==
    LET MC_TEExpression == INSTANCE MC_TEExpression
    IN MC_TEExpression!expression
----

_trace ==
    LET MC_TETrace == INSTANCE MC_TETrace
    IN MC_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        cur = ((t1 :> [id |-> 0, from |-> "", to |-> "", amt |-> 0] @@ t2 :> [id |-> 0, from |-> "", to |-> "", amt |-> 0]))
        /\
        res = ((t1 :> "-" @@ t2 :> "-"))
        /\
        lsn = ((t1 :> 0 @@ t2 :> 0))
        /\
        acks = ({[res |-> "BALANCE", lsn |-> 1, req |-> [id |-> 0, from |-> "A", to |-> "A", amt |-> 1]]})
        /\
        crashes = (1)
        /\
        bal = ([A |-> 2, B |-> 0])
        /\
        inflight = (<<>>)
        /\
        disk = (<<>>)
        /\
        idem = ({})
        /\
        buf = (<<>>)
        /\
        pc = ((t1 :> "idle" @@ t2 :> "idle"))
        /\
        ops = ((t1 :> 1 @@ t2 :> 1))
        /\
        written = (FALSE)
        /\
        up = (FALSE)
    )
----

_init ==
    /\ bal = _TETrace[1].bal
    /\ cur = _TETrace[1].cur
    /\ written = _TETrace[1].written
    /\ pc = _TETrace[1].pc
    /\ acks = _TETrace[1].acks
    /\ disk = _TETrace[1].disk
    /\ inflight = _TETrace[1].inflight
    /\ res = _TETrace[1].res
    /\ lsn = _TETrace[1].lsn
    /\ idem = _TETrace[1].idem
    /\ buf = _TETrace[1].buf
    /\ crashes = _TETrace[1].crashes
    /\ up = _TETrace[1].up
    /\ ops = _TETrace[1].ops
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ bal  = _TETrace[i].bal
        /\ bal' = _TETrace[j].bal
        /\ cur  = _TETrace[i].cur
        /\ cur' = _TETrace[j].cur
        /\ written  = _TETrace[i].written
        /\ written' = _TETrace[j].written
        /\ pc  = _TETrace[i].pc
        /\ pc' = _TETrace[j].pc
        /\ acks  = _TETrace[i].acks
        /\ acks' = _TETrace[j].acks
        /\ disk  = _TETrace[i].disk
        /\ disk' = _TETrace[j].disk
        /\ inflight  = _TETrace[i].inflight
        /\ inflight' = _TETrace[j].inflight
        /\ res  = _TETrace[i].res
        /\ res' = _TETrace[j].res
        /\ lsn  = _TETrace[i].lsn
        /\ lsn' = _TETrace[j].lsn
        /\ idem  = _TETrace[i].idem
        /\ idem' = _TETrace[j].idem
        /\ buf  = _TETrace[i].buf
        /\ buf' = _TETrace[j].buf
        /\ crashes  = _TETrace[i].crashes
        /\ crashes' = _TETrace[j].crashes
        /\ up  = _TETrace[i].up
        /\ up' = _TETrace[j].up
        /\ ops  = _TETrace[i].ops
        /\ ops' = _TETrace[j].ops

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("MC_TTrace_1794856924.json", _TETrace)

=============================================================================

 Note that you can extract this module `MC_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `MC_TEExpression.tla` file takes precedence 
  over the module `MC_TEExpression` below).

---- MODULE MC_TEExpression ----
EXTENDS Sequences, TLCExt, MC, Toolbox, Naturals, TLC, MC_TEConstants

expression == 
    [
        \* To hide variables of the `MC` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        bal |-> bal
        ,cur |-> cur
        ,written |-> written
        ,pc |-> pc
        ,acks |-> acks
        ,disk |-> disk
        ,inflight |-> inflight
        ,res |-> res
        ,lsn |-> lsn
        ,idem |-> idem
        ,buf |-> buf
        ,crashes |-> crashes
        ,up |-> up
        ,ops |-> ops
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_balUnchanged |-> bal = bal'
        
        \* Format the `bal` variable as Json value.
        \* ,_balJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(bal)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_balModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].bal # _TETrace[s-1].bal
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE MC_TETrace ----
\*EXTENDS IOUtils, MC, TLC, MC_TEConstants
\*
\*trace == IODeserialize("MC_TTrace_1794856924.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE MC_TETrace ----
EXTENDS MC, TLC, MC_TEConstants

trace == 
    <<
    ([cur |-> (t1 :> [id |-> 0, from |-> "", to |-> "", amt |-> 0] @@ t2 :> [id |-> 0, from |-> "", to |-> "", amt |-> 0]),res |-> (t1 :> "-" @@ t2 :> "-"),lsn |-> (t1 :> 0 @@ t2 :> 0),acks |-> {},crashes |-> 0,bal |-> [A |-> 2, B |-> 0],inflight |-> <<>>,disk |-> <<>>,idem |-> {},buf |-> <<>>,pc |-> (t1 :> "idle" @@ t2 :> "idle"),ops |-> (t1 :> 0 @@ t2 :> 0),written |-> FALSE,up |-> TRUE]),
    ([cur |-> (t1 :> [id |-> 1, from |-> "A", to |-> "B", amt |-> 1] @@ t2 :> [id |-> 0, from |-> "", to |-> "", amt |-> 0]),res |-> (t1 :> "-" @@ t2 :> "-"),lsn |-> (t1 :> 0 @@ t2 :> 0),acks |-> {},crashes |-> 0,bal |-> [A |-> 2, B |-> 0],inflight |-> <<>>,disk |-> <<>>,idem |-> {},buf |-> <<>>,pc |-> (t1 :> "locked" @@ t2 :> "idle"),ops |-> (t1 :> 1 @@ t2 :> 0),written |-> FALSE,up |-> TRUE]),
    ([cur |-> (t1 :> [id |-> 1, from |-> "A", to |-> "B", amt |-> 1] @@ t2 :> [id |-> 0, from |-> "", to |-> "", amt |-> 0]),res |-> (t1 :> "OK" @@ t2 :> "-"),lsn |-> (t1 :> 1 @@ t2 :> 0),acks |-> {},crashes |-> 0,bal |-> [A |-> 1, B |-> 1],inflight |-> <<>>,disk |-> <<>>,idem |-> {[id |-> 1, from |-> "A", to |-> "B", amt |-> 1]},buf |-> <<[id |-> 1, from |-> "A", to |-> "B", amt |-> 1]>>,pc |-> (t1 :> "wait" @@ t2 :> "idle"),ops |-> (t1 :> 1 @@ t2 :> 0),written |-> FALSE,up |-> TRUE]),
    ([cur |-> (t1 :> [id |-> 1, from |-> "A", to |-> "B", amt |-> 1] @@ t2 :> [id |-> 0, from |-> "A", to |-> "A", amt |-> 1]),res |-> (t1 :> "OK" @@ t2 :> "BALANCE"),lsn |-> (t1 :> 1 @@ t2 :> 1),acks |-> {},crashes |-> 0,bal |-> [A |-> 1, B |-> 1],inflight |-> <<>>,disk |-> <<>>,idem |-> {[id |-> 1, from |-> "A", to |-> "B", amt |-> 1]},buf |-> <<[id |-> 1, from |-> "A", to |-> "B", amt |-> 1]>>,pc |-> (t1 :> "wait" @@ t2 :> "wait"),ops |-> (t1 :> 1 @@ t2 :> 1),written |-> FALSE,up |-> TRUE]),
    ([cur |-> (t1 :> [id |-> 1, from |-> "A", to |-> "B", amt |-> 1] @@ t2 :> [id |-> 0, from |-> "A", to |-> "A", amt |-> 1]),res |-> (t1 :> "OK" @@ t2 :> "BALANCE"),lsn |-> (t1 :> 1 @@ t2 :> 1),acks |-> {[res |-> "BALANCE", lsn |-> 1, req |-> [id |-> 0, from |-> "A", to |-> "A", amt |-> 1]]},crashes |-> 0,bal |-> [A |-> 1, B |-> 1],inflight |-> <<>>,disk |-> <<>>,idem |-> {[id |-> 1, from |-> "A", to |-> "B", amt |-> 1]},buf |-> <<[id |-> 1, from |-> "A", to |-> "B", amt |-> 1]>>,pc |-> (t1 :> "wait" @@ t2 :> "idle"),ops |-> (t1 :> 1 @@ t2 :> 1),written |-> FALSE,up |-> TRUE]),
    ([cur |-> (t1 :> [id |-> 0, from |-> "", to |-> "", amt |-> 0] @@ t2 :> [id |-> 0, from |-> "", to |-> "", amt |-> 0]),res |-> (t1 :> "-" @@ t2 :> "-"),lsn |-> (t1 :> 0 @@ t2 :> 0),acks |-> {[res |-> "BALANCE", lsn |-> 1, req |-> [id |-> 0, from |-> "A", to |-> "A", amt |-> 1]]},crashes |-> 1,bal |-> [A |-> 2, B |-> 0],inflight |-> <<>>,disk |-> <<>>,idem |-> {},buf |-> <<>>,pc |-> (t1 :> "idle" @@ t2 :> "idle"),ops |-> (t1 :> 1 @@ t2 :> 1),written |-> FALSE,up |-> FALSE])
    >>
----


=============================================================================

---- MODULE MC_TEConstants ----
EXTENDS MC

CONSTANTS t1, t2

=============================================================================

---- CONFIG MC_TTrace_1794856924 ----
CONSTANTS
    Accounts <- MCAccounts
    Init <- MCInit
    Reqs <- MCReqs
    Threads = { t1 , t2 }
    BufCap = 1
    MaxOps = 2
    MaxCrashes = 1
    Bug = "ReadBeforeDurable"
    t1 = t1
    t2 = t2

INVARIANT
    _inv

CHECK_DEADLOCK
    \* CHECK_DEADLOCK off because of PROPERTY or INVARIANT above.
    FALSE

INIT
    _init

NEXT
    _next

CONSTANT
    _TETrace <- _trace

ALIAS
    _expression
=============================================================================
\* Generated on Mon Nov 16 19:22:05 UTC 2026