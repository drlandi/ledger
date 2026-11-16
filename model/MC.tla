--------------------------------- MODULE MC ---------------------------------
(* The small instance TLC checks: 2 accounts, 3 requests, 2 threads.        *)
(*   r1  id 1: A -> B, 1                                                    *)
(*   r2  id 2: B -> A, 1   only possible after r1 delivered (B starts at 0) *)
(*   r3  id 1: A -> B, 2   same id as r1 with another amount                *)
EXTENDS Ledger

MCAccounts == {"A", "B"}
MCInit     == [a \in MCAccounts |-> IF a = "A" THEN 2 ELSE 0]
MCReqs     == { [id |-> 1, from |-> "A", to |-> "B", amt |-> 1],
                [id |-> 2, from |-> "B", to |-> "A", amt |-> 1],
                [id |-> 1, from |-> "A", to |-> "B", amt |-> 2] }
=============================================================================
