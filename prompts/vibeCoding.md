Revise the GAL bus-reduction pseudocode according to the following notes.

General assumptions:

- At the beginning, all roots have arity 3:

  - the top root has a bus with three wires;
  - each free-variable root has a bus with three wires.

- During reduction, when applying the reduction rules from Figure 2, the number of wires in a bus may increase or decrease.

- Figure 2 contains six reduction-rule schemas. Let us number them as follows:
  - Rule 1: top row, leftmost rule.
  - Rule 2: top row, second rule from the left.
  - Rule 3: top row, third rule from the left.
  - Rule 4: bottom row, leftmost rule.
  - Rule 5: bottom row, second rule from the left.
  - Rule 6: bottom row, third rule from the left.

Rule schemas:

- Rule 1: Fan/fan interaction on the same wire.

  - The number of wires to the left and right of the fan is arbitrary.
  - The number of wires on all fan ports must be the same.
  - The two fans must interact on the same wire.

- Rule 2: Bracket/bracket interaction on the same wire.

  - The number of wires to the left and right of the bracket is arbitrary.
  - These numbers must be the same for the top and bottom bracket.
  - The two brackets must interact on the same wire.

- Rule 3: Croissant/croissant interaction on the same wire.

  - The number of wires to the left and right of the croissant is arbitrary.
  - These numbers must be the same for the top and bottom croissants.
  - The result is annihilation: both croissants are removed from the graph.

- Rule 4: Fan/fan interaction on different wires.

  - The number of wires to the left and right of each fan is arbitrary.
  - The number of wires on all fan ports must be the same.
  - The two fans must interact on different wires.
  - The relative order matters: the top fan is to the right of the bottom fan.
  - The fan wires do not need to be adjacent. For example, the rule applies if one fan acts on wire 2 and the other acts on wire 5.

- Rule 5: Fan/bracket interaction on different wires.

  - The number of wires to the left and right of the fan/bracket pair is arbitrary.
  - The number of wires between the bracket and the fan on the left-hand side of the rule must be preserved.
  - It does not matter whether the bracket is to the left of the fan or vice versa.
  - The fan and the bracket must not be on the same wire.

- Rule 6: Fan/croissant interaction on different wires.
  - The number of wires to the left and right of the fan/croissant pair is arbitrary.
  - The number of wires between the croissant and the fan on the left-hand side of the rule must be preserved.
  - It does not matter whether the croissant is to the left of the fan or vice versa.
  - The fan and the croissant must not be on the same wire.

Width-changing behavior:

- In Rule 2, the number of wires in the bus edge between the two brackets increases by 1.

- In Rule 5, the number of wires in the bus edge between the bracket and the fan increases by 1.

- In Rule 6, the number of wires at all ports of the fan decreases by 1.
