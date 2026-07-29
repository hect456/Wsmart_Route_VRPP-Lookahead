# Mathematical formulation 2 — VRPP + Lookahead with an operational working day

Complete specification of the model implemented in
[src/vrpp_lookahead/vrpp.py](src/vrpp_lookahead/vrpp.py),
[src/vrpp_lookahead/lookahead.py](src/vrpp_lookahead/lookahead.py),
[src/vrpp_lookahead/config.py](src/vrpp_lookahead/config.py) and
[src/vrpp_lookahead/instance.py](src/vrpp_lookahead/instance.py).

**What this document adds over [formulation.md](formulation.md).** The first
formulation prices distance and never time. It measures travel time, reports it,
and constrains nothing — so nothing stops it from returning a route no crew can
finish. This version closes that hole: it introduces a service time per bin, an
average round speed, and a hard limit on the length of the working day, all as
constraints inside the MILP rather than as checks applied afterwards. Sections
[3.4](#34-working-day-parameters), [4.2](#42-arc-traversal-time),
[6](#6-decision-variables), [8.10–8.13](#810-shift-arc-activation) and
[10](#10-what-the-working-day-changes) are new or materially rewritten;
everything else is carried over so this file stands on its own.

This document describes what the code actually solves, not an idealised version
of it. Every equation carries a pointer to the lines that build it, and
[section 12](#12-what-the-model-still-does-not-capture) states plainly what is
still left out.

---

## Table of contents

1. [Problem statement](#1-problem-statement)
2. [Sets and indices](#2-sets-and-indices)
3. [Input data and parameters](#3-input-data-and-parameters)
4. [Derived quantities](#4-derived-quantities)
5. [Lookahead classification](#5-lookahead-classification)
6. [Decision variables](#6-decision-variables)
7. [Objective function](#7-objective-function)
8. [Constraints](#8-constraints)
9. [Pre-processing](#9-pre-processing)
10. [What the working day changes](#10-what-the-working-day-changes)
11. [Multi-day simulation](#11-multi-day-simulation)
12. [What the model still does not capture](#12-what-the-model-still-does-not-capture)
13. [Model size and solution method](#13-model-size-and-solution-method)
14. [Equation-to-code map](#14-equation-to-code-map)

---

## 1. Problem statement

A fleet of identical vehicles based at a single depot collects waste from a set
of bins spread over a road network. Unlike a classical VRP, **not every bin has
to be visited**: each bin carries a *profit* proportional to the waste it holds,
and the operator is free to skip a bin whose collection does not pay for the
detour. This is the **Vehicle Routing Problem with Profits (VRPP)**, in its
*prize-collecting* variant — the objective trades revenue against travel cost
rather than minimising distance under full-coverage constraints.

Three things make this instance specific:

* **Mandatory subset.** Bins that would overflow before the next visit are
  forced into the solution regardless of profitability. These come from the
  *lookahead* classification of [section 5](#5-lookahead-classification).
* **Fleet-size decision.** The number of routes actually used, `k`, is itself a
  variable bounded by `MAX_ROUTES`, and each vehicle used costs a fixed `OMEGA`.
* **A bounded working day.** Every route must be completable by one crew inside
  a shift of `max_shift_h` hours, counting both driving and the time spent
  emptying containers. This is what separates formulation 2 from formulation 1.

The result is a single mixed-integer linear program solved with Gurobi, one per
simulated day.

### Why the working day is a constraint and not a report

In formulation 1 a stop is free. Serving a bin costs only the detour needed to
reach it, so once bins are dense — as they are in an urban cluster — the model
adds stops almost without limit until the vehicle capacity runs out. On the
reference instance that produced two routes of **9.58 h and 8.03 h**: profitable
on paper, undeliverable in practice.

Two distinct omissions caused it, and both are fixed here:

1. **No service time.** Emptying a container takes a crew a fixed amount of time
   regardless of distance. Without it, 300 stops cost the same as 100.
2. **No duration limit.** Nothing bounded the sum of driving and service.

Adding the first without the second would only change the accounting. Adding
both changes which solution is optimal — which is the point.

---

## 2. Sets and indices

| Symbol | Definition |
|---|---|
| $N = \{0, 1, \dots, n\}$ | all nodes; index $0$ is the depot |
| $N_R = N \setminus \{0\}$ | collection points (bins), $\lvert N_R \rvert = n$ |
| $A \subseteq N \times N$ | arc set after pre-filtering, $i \neq j$ |
| $\mathcal{MG} \subseteq N_R$ | MustGo bins — overflow tomorrow |
| $\mathcal{LA} \subseteq N_R$ | MustGo-LookAhead bins — overflow within the horizon |
| $\mathcal{F} = \mathcal{MG} \cup \mathcal{LA}$ | forced bins, after the capacity adjustment of [section 9.2](#92-mustgo-adjustment-to-the-available-fleet) |

Node $0$ is the depot both as origin and as destination: every route is a closed
walk $0 \rightarrow \cdots \rightarrow 0$.

The arc set $A$ is **not** the complete digraph. It is reduced by the filtering
of [section 9.1](#91-arc-filtering), which is what makes an instance of ~500
nodes tractable.

---

## 3. Input data and parameters

### 3.1 Per-bin data, read from the attributes file

| Symbol | Column | Unit | Meaning |
|---|---|---|---|
| $\mathrm{Ncont}_i$ | `Ncont` | — | number of containers standing at point $i$ |
| $\mathrm{Vol}_i$ | `Vol_cont` | m³ | volume of **one** container at point $i$ |
| $\mathrm{Vkg}_i$ | `Vol_kg` | kg | waste currently held at point $i$ |
| $a_i$ | `ai` | %/day | daily fill rate, as a percentage of point capacity |

### 3.2 Network data, from the OpenRouteService matrix

| Symbol | Unit | Meaning |
|---|---|---|
| $D_{ij}$ | km | road distance from $i$ to $j$ |
| $T_{ij}$ | min | road travel time from $i$ to $j$ |

$D$ is **asymmetric** in general ($D_{ij} \neq D_{ji}$), because it comes from a
real directed road network with one-way streets. The formulation is stated over
a directed graph for exactly this reason.

### 3.3 Economic and fleet parameters, from the YAML `model:` block

| Symbol | Key | Unit | Meaning |
|---|---|---|---|
| $B$ | `B` | kg/m³ | waste density — converts volume into weight |
| $Q$ | `Q` | kg | vehicle capacity |
| $R$ | `R` | €/kg | revenue per kg collected |
| $C$ | `C` | €/km | travel cost |
| $\Omega$ | `OMEGA` | € | fixed cost per vehicle used |
| $K^{\max}$ | `MAX_ROUTES` | — | maximum number of routes |
| $\theta^{MG}$ | `threshold_mg` | % | MustGo threshold |
| $\theta^{OVF}$ | `threshold_overflow` | % | overflow threshold for the lookahead |
| $W$ | `window` | days | lookahead horizon |

### 3.4 Working-day parameters, from the YAML `shift:` block

New in this formulation. Defined in `Shift`
([config.py](src/vrpp_lookahead/config.py)).

| Symbol | Key | Unit | Meaning |
|---|---|---|---|
| $v$ | `speed_kmh` | km/h | average speed of the collection round |
| $s$ | `service_time_min` | min | time spent emptying the containers at one point |
| $H$ | `max_shift_h` | h | maximum length of one crew's working day |
| — | `enforce` | bool | `true` imposes the shift as a hard constraint; `false` only reports it |
| — | `report_h` | h | list of shift lengths reported as fits / exceeds |

Three modelling decisions are embedded in these five keys, and each is worth
defending explicitly.

**Why $v$ overrides the ORS travel times $T_{ij}$.** ORS returns free-flow
driving times for the vehicle profile. A collection round does not achieve them:
it stops every few hundred metres, manoeuvres a heavy vehicle beside a
container, and restarts. On the reference instance the ORS times imply about
34 km/h, which is optimistic for this duty cycle. A single average round speed
is an assumption the operator can state, defend and calibrate against their own
records — a per-arc time from a routing API cannot be calibrated the same way.
The consequence is that $T_{ij}$ survives in the reports as a reference figure
but plays no part in the constraint.

**Why $s$ is constant across bins.** The service time realistically varies with
the number of containers $\mathrm{Ncont}_i$ standing at the point. Making $s$
proportional to $\mathrm{Ncont}_i$ is a one-line change to
[equation 8.12](#812-shift-time-propagation) and does not alter the structure of
the model. A constant is used because the operator supplies one figure, not a
per-point breakdown, and inventing the breakdown would give false precision.

**Why `enforce` is a switch rather than always on.** With `enforce: false` the
model is exactly formulation 1 and reproduces its published results, while the
reports still carry the working-day columns. This keeps a single code path for
both formulations and makes the effect of the constraint measurable by flipping
one key — see [section 10](#10-what-the-working-day-changes).

---

## 4. Derived quantities

### 4.1 Per-bin quantities

Computed once in [instance.py](src/vrpp_lookahead/instance.py), and used
everywhere afterwards:

$$
\mathrm{CAP}_i \;=\; B \cdot \mathrm{Ncont}_i \cdot \mathrm{Vol}_i
\qquad \text{[kg]}
$$

$$
S_i \;=\; \mathrm{Vkg}_i
\qquad \text{[kg]}
$$

$$
\alpha_i \;=\; \frac{a_i}{100} \cdot \mathrm{CAP}_i
\qquad \text{[kg/day]}
$$

* $\mathrm{CAP}_i$ — total capacity of point $i$ in kilograms. A point may hold
  several containers, hence the $\mathrm{Ncont}_i$ factor. The density $B$ is
  what turns the container volume into a weight.
* $S_i$ — waste held at point $i$ at the start of the day. This is the quantity
  that generates revenue if the point is served, so it plays the role of the
  *prize* in the VRPP. Note $S_0 = 0$ for the depot.
* $\alpha_i$ — kilograms accumulated per day at point $i$.

A degenerate point with $\mathrm{Ncont}_i = 0$ or $\mathrm{Vol}_i = 0$ would give
$\mathrm{CAP}_i = 0$ and make the fill ratio undefined; the loader rejects the
instance in that case.

### 4.2 Arc traversal time

New in this formulation. For every arc in the filtered set:

$$
\tau_{ij} \;=\; \frac{D_{ij}}{v} \cdot 60
\qquad \text{[min]}, \qquad (i,j) \in A
$$

and the shift limit is carried in minutes to match:

$$
H^{\min} \;=\; 60\,H
\qquad \text{[min]}
$$

The full time cost of traversing $(i,j)$ and serving $j$ is $\tau_{ij} + s$. The
helper `Shift.route_min(km, n)` in [config.py](src/vrpp_lookahead/config.py)
evaluates the same expression for a whole route, and is the single place where
the working day is defined — the solver, the per-route KPI, the fixed-route
evaluation of external solutions and the reports all call it, so no two parts of
the project can disagree about what an hour of work means.

---

## 5. Lookahead classification

Run before the optimisation, on the state at the start of the day. Let
$\ell_i$ be the current level of bin $i$ in kg (on day 1, $\ell_i = S_i$).

**MustGo** — will reach the threshold tomorrow:

$$
i \in \mathcal{MG}
\iff
\ell_i + \alpha_i \;\ge\; \frac{\theta^{MG}}{100}\,\mathrm{CAP}_i
$$

**MustGo-LookAhead** — not MustGo, but will overflow somewhere inside the
horizon:

$$
i \in \mathcal{LA}
\iff
i \notin \mathcal{MG}
\;\wedge\;
\exists\, k \in \{2, \dots, W\} :\;
\ell_i + k\,\alpha_i \;\ge\; \frac{\theta^{OVF}}{100}\,\mathrm{CAP}_i
$$

Everything else is **Optional**: the solver collects it only if it pays.

With the default $\theta^{MG} = \theta^{OVF} = 100\,\%$ the two rules read
naturally: *MustGo* overflows tomorrow, *MustGoLA* overflows within $W$ days.
Lowering $\theta^{MG}$ makes the policy more conservative, forcing collection
before the bin is physically full.

The economic rationale for $\mathcal{LA}$ is that a bin near a route being
driven today is far cheaper to empty now than to reach with a dedicated trip in
three days. The classification promotes it to mandatory so the optimiser cannot
postpone it into a more expensive future.

> **Both sets are forced.** In the code the mandatory set is
> $\mathcal{F} = \mathcal{MG} \cup \mathcal{LA}$ — MustGoLA bins are constrained
> exactly as hard as MustGo ones. The distinction survives only in the reports,
> through the *original* classification. This is why the solver's own per-route
> KPI shows every forced bin as "MustGo": see
> [section 14](#14-equation-to-code-map).

> **The shift interacts with this classification.** Constraint 8.8 forces every
> bin in $\mathcal{F}$ and constraint 8.13 bounds the working day. If the forced
> set cannot be served inside $K^{\max}$ shifts, the two are jointly infeasible
> — a failure mode formulation 1 could not have, and one the fleet-capacity
> adjustment of section 9.2 does **not** detect, because it reasons about
> kilograms only. See [section 10.3](#103-a-feasibility-trap-worth-knowing-about).

---

## 6. Decision variables

| Variable | Domain | Meaning |
|---|---|---|
| $x_{ij}$ | $\{0,1\}$, $(i,j) \in A$ | arc $(i,j)$ is traversed |
| $g_i$ | $\{0,1\}$, $i \in N_R$ | bin $i$ is collected |
| $y_{ij}$ | $\mathbb{R}_{\ge 0}$, $(i,j) \in A$ | load carried along arc $(i,j)$ [kg] |
| $f_{ij}$ | $\mathbb{R}_{\ge 0}$, $(i,j) \in A$ | unit flow along arc $(i,j)$ — connectivity only |
| $t_{ij}$ | $\mathbb{R}_{\ge 0}$, $(i,j) \in A$ | **elapsed minutes on arrival at $j$ via arc $(i,j)$** |
| $k$ | $\mathbb{Z}$, $0 \le k \le K^{\max}$ | number of routes used |

The variable $t_{ij}$ is new in this formulation and is created **only when
`shift.enforce` is true** — with the switch off the model is literally
formulation 1, not a relaxation of formulation 2.

Three flow variables now run over the same arc set, and they are deliberately
kept apart:

* $y_{ij}$ is **physical**: kilograms actually in the truck on that arc. It
  enforces the capacity $Q$.
* $f_{ij}$ is **fictitious**: one unit of an abstract commodity sent from the
  depot to every served bin. It has no physical meaning and exists solely to
  forbid subtours. Merging it with $y$ would be possible but would weaken the
  linear relaxation, because a bin with $S_i = 0$ would carry no physical load
  and could then sit on a disconnected cycle.
* $t_{ij}$ is **temporal**: minutes elapsed since the crew left the depot,
  measured at the moment of arriving at $j$. It enforces the shift.

All three share one structure — a single-commodity flow accumulated along the
route — which is why the same accounting trick works three times. What differs
is what each accumulates: $y$ gains $S_i$ at each node, $f$ gains one unit, and
$t$ gains $s$ at each node plus $\tau_{ij}$ on each arc.

---

## 7. Objective function

$$
\max \;\;
\underbrace{R \sum_{i \in N_R} S_i\, g_i}_{\text{revenue}}
\;-\;
\underbrace{C \sum_{(i,j) \in A} D_{ij}\, x_{ij}}_{\text{travel cost}}
\;-\;
\underbrace{\Omega \, k}_{\text{fleet cost}}
$$

Maximising **net profit in euros**, not minimising distance. Three consequences
worth stating explicitly:

* A bin is collected only when its marginal revenue $R\,S_i$ exceeds the
  marginal detour cost $C \cdot \Delta D$ — unless it is forced.
* Because $R$ multiplies $S_i$, a full bin is worth more than an empty one, so
  the model naturally prefers dense, full points.
* The term $\Omega k$ makes the model *prefer fewer vehicles*, all else equal.
  With $\Omega = 0.1$ € this is a tie-breaker rather than a real driver; raising
  it materially changes the fleet-size decision.

> **The objective is unchanged from formulation 1: time still carries no price.**
> This is intentional and it is not an oversight. Crew time enters as a *budget*
> (constraint 8.13), not as a cost. The model may therefore use the full shift
> whenever doing so is profitable, and it will: on the reference instance both
> routes come out at 8.00 h of an 8 h limit.
>
> If instead the operator pays per hour worked, the honest change is to add a
> term $-\,c^{\text{crew}} \sum_{(i,j) \in A} \tau_{ij} x_{ij} - c^{\text{crew}}
> s \sum_{i \in N_R} g_i$ to the objective. That is a different model with a
> different optimum: it would trade hours against kilograms rather than spending
> every available hour. Which of the two is right depends on whether the crew is
> paid by the shift or by the hour — a fact about the contract, not about the
> mathematics.

---

## 8. Constraints

Constraints 8.1 – 8.9 are carried over from formulation 1. Constraints
8.10 – 8.13 are new and are added **only if `shift.enforce` is true**.

### 8.1 Fleet size

$$
k \;\le\; K^{\max}
$$

Also imposed as a variable bound. With a small $K^{\max}$ the fleet capacity
$K^{\max} \cdot Q$ may be unable to hold all forced bins, which triggers the
adjustment of [section 9.2](#92-mustgo-adjustment-to-the-available-fleet).

### 8.2 Degree constraints — visiting equals collecting

$$
\sum_{i \,:\, (i,j) \in A} x_{ij} \;=\; g_j
\qquad \forall j \in N_R
$$

$$
\sum_{t \,:\, (j,t) \in A} x_{jt} \;=\; g_j
\qquad \forall j \in N_R
$$

In-degree and out-degree of every bin equal its selection variable. A served bin
is entered exactly once and left exactly once; an unserved bin is isolated.

This couples $x$ and $g$ tightly, and it means **a vehicle cannot drive through
a bin without emptying it** — there is no "pass-through" state in this model.
The consequence is sharper once time is constrained: a bin that lies on the
cheapest path between two served bins cannot be bypassed for free, so the
geometry of a route and the set of bins it serves are the same decision.

### 8.3 Depot departures and arrivals

$$
k \;=\; \sum_{j \,:\, (0,j) \in A} x_{0j}
\qquad\qquad
\sum_{j \,:\, (j,0) \in A} x_{j0} \;=\; k
$$

The number of routes is defined as the number of arcs leaving the depot, and as
many must return. Together with the degree constraints this makes every
connected component containing served bins a closed walk through the depot.

### 8.4 Capacity and arc activation

$$
y_{ij} \;\le\; Q\, x_{ij}
\qquad \forall (i,j) \in A
$$

$$
f_{ij} \;\le\; \lvert N_R \rvert \, x_{ij}
\qquad \forall (i,j) \in A
$$

Both flows may only run on traversed arcs, and the physical load never exceeds
the vehicle capacity. The big-$M$ of the second constraint is $\lvert N_R
\rvert$, the largest number of bins a single route could possibly serve.

### 8.5 Load propagation

$$
\sum_{j \,:\, (i,j) \in A} y_{ij}
\;-\;
\sum_{j \,:\, (j,i) \in A} y_{ji}
\;=\;
S_i\, g_i
\qquad \forall i \in N_R
$$

The load leaving bin $i$ minus the load entering it equals what was picked up
there. Chained along a route, this accumulates the collected weight, and
combined with $y_{ij} \le Q x_{ij}$ it caps each route at $Q$ kilograms without
any explicit per-route capacity constraint.

### 8.6 Vehicles leave the depot empty

$$
y_{0j} \;=\; 0
\qquad \forall j \,:\, (0,j) \in A
$$

Without this, the load could be initialised arbitrarily and the capacity
constraint would be vacuous.

### 8.7 Load lower bound (valid inequality)

$$
y_{ij} \;\ge\; S_i \, x_{ij}
\qquad \forall (i,j) \in A,\; i \neq 0
$$

Not required for correctness — it is implied by 8.5 and 8.6 for integral
solutions — but it **strengthens the linear relaxation** considerably. If arc
$(i,j)$ is used then $g_i = 1$ by 8.2, so the truck leaves $i$ carrying at least
the $S_i$ it just picked up. Stating it explicitly stops the LP from splitting
load fractionally across arcs.

### 8.8 Mandatory bins

$$
g_i \;=\; 1
\qquad \forall i \in \mathcal{F}
$$

Where $\mathcal{F}$ is the forced set **after** the adjustment of section 9.2.
Bins demoted by that adjustment lose this constraint and revert to optional —
the solver may still pick them up if profitable.

### 8.9 Subtour elimination — single-commodity flow

$$
\sum_{j \,:\, (0,j) \in A} f_{0j}
\;=\;
\sum_{j \in N_R} g_j
$$

$$
\sum_{i \,:\, (i,j) \in A} f_{ij}
\;-\;
\sum_{t \,:\, (j,t) \in A} f_{jt}
\;=\;
g_j
\qquad \forall j \in N_R
$$

The depot injects exactly one unit of flow per served bin, and every served bin
absorbs exactly one unit. Any cycle disconnected from the depot would have to
absorb flow it never receives, so it is infeasible.

This **Gavish–Graves** style formulation is chosen over Miller–Tucker–Zemlin
because it gives a tighter relaxation, and over exponential subtour-elimination
cuts because it keeps the model in a single solve without callbacks. Its cost is
$\lvert A \rvert$ extra continuous variables.

---

### 8.10 Shift — arc activation

$$
t_{ij} \;\le\; H^{\min}\, x_{ij}
\qquad \forall (i,j) \in A
$$

Time may only elapse on arcs actually traversed, and no arrival can be later
than the end of the shift. This is the big-$M$ linking for $t$, exactly parallel
to $y_{ij} \le Q x_{ij}$, with $H^{\min}$ playing the role of $Q$.

### 8.11 Shift — departure from the depot

$$
t_{0j} \;=\; \tau_{0j}\, x_{0j}
\qquad \forall j \,:\, (0,j) \in A
$$

Every crew starts its shift at the depot with the clock at zero, so the elapsed
time on arrival at the first bin is exactly the travel time of that first arc.
This is the temporal counterpart of 8.6 — without it, the clock could be
initialised anywhere and the shift bound would be vacuous.

Note this is an **equality**, not an upper bound: fixing the start makes the
accumulated time along the route exact rather than merely bounded, which is what
lets constraint 8.13 be stated on a single arc.

### 8.12 Shift — time propagation

$$
\sum_{j \,:\, (i,j) \in A} t_{ij}
\;-\;
\sum_{j \,:\, (j,i) \in A} t_{ji}
\;=\;
s\, g_i
\;+\;
\sum_{j \,:\, (i,j) \in A} \tau_{ij}\, x_{ij}
\qquad \forall i \in N_R
$$

The core of the working-day model, and the exact analogue of the load
propagation 8.5. Read it at a single served bin $i$: the crew arrives with
$t_{ji}$ minutes on the clock, spends $s$ minutes emptying the containers, and
drives $\tau_{ij}$ minutes to the next bin, arriving there with
$t_{ij} = t_{ji} + s + \tau_{ij}$.

Because 8.2 forces exactly one outgoing arc when $g_i = 1$, the sum
$\sum_j \tau_{ij} x_{ij}$ collapses to the travel time of the one arc actually
taken. When $g_i = 0$ the bin is isolated, every incident $x$ and $t$ is zero,
and the constraint reads $0 = 0$.

If service time should scale with the number of containers at the point, replace
$s\,g_i$ by $s\,\mathrm{Ncont}_i\,g_i$. Nothing else in the formulation changes.

### 8.13 Shift — the working day is bounded

$$
t_{j0} \;\le\; H^{\min}\, x_{j0}
\qquad \forall j \,:\, (j,0) \in A
$$

This is a special case of 8.10, stated separately because it is the constraint
that does the work. By 8.11 and 8.12 the quantity $t_{j0}$ is the total elapsed
time of the route when the crew arrives back at the depot — driving out, every
service stop, and the return leg included. Bounding it therefore bounds the
entire working day.

Two properties of this construction are worth stating:

* **The return leg is counted.** A formulation that bounded time at the last bin
  instead would let a route end far from the depot at no cost. Here the arc
  $(j,0)$ carries its own $\tau_{j0}$ through 8.12, so getting home is paid for.
* **The bound is per route, not aggregate.** Each route arrives at the depot on
  its own arc, and each such arc is bounded separately. Two routes of 8 h are
  feasible; one route of 16 h is not. This is the right reading of a shift — it
  limits one crew, and two crews working in parallel do not add up.

> **Why a flow formulation rather than big-$M$ sequencing.** The textbook
> alternative is $u_j \ge u_i + s + \tau_{ij} - M(1 - x_{ij})$ over node
> variables $u$. It needs one big-$M$ constraint per arc with $M \approx
> H^{\min}$, and its linear relaxation is weak: fractional $x$ switches the
> constraint off almost entirely. The flow form above reuses the structure
> already proven on $y$ and $f$, keeps every coefficient at its natural scale,
> and adds $\lvert A \rvert$ continuous variables instead of $n$ — a trade of
> memory for tightness that is worth making here, though it is not free: see
> [section 13](#13-model-size-and-solution-method).

---

## 9. Pre-processing

Three transformations run before the solve. The first two change the feasible
region; the third only helps the search.

### 9.1 Arc filtering

The complete digraph on 492 nodes has 241 572 arcs, which is impractical. The
arc set $A$ keeps:

1. **Every arc incident to the depot**, $(0,j)$ and $(j,0)$ — unconditionally,
   since removing them could make the problem infeasible.
2. **Bidirectional $\kappa$-nearest-neighbour arcs**: $(i,j)$ survives if $j$ is
   among the $\kappa$ closest nodes to $i$ *or* $i$ is among the $\kappa$
   closest to $j$, with $\kappa =$ `solver.knn`. The disjunction matters — a
   one-sided rule would break symmetry in a way that discards good arcs in
   sparse areas.
3. Optionally, with `solver.keep_mustgo_arcs = true`, every arc between two
   forced bins. Off by default, to reproduce the original notebooks exactly.

And then **removes** any surviving arc with

$$
S_i + S_j \;>\; Q
$$

since two bins whose combined content exceeds the vehicle capacity can never be
consecutive on a feasible route.

> This last rule only bites when $Q$ is small relative to the bin levels. On the
> reference instance with $Q = 4000$ kg and a maximum bin content of 120 kg it
> removes **zero** arcs; the reduction there comes entirely from the
> $\kappa$-nearest-neighbour rule.

> **This is a heuristic restriction, not a valid reduction.** Filtering by
> $\kappa$-nearest neighbours can in principle exclude the true optimum. The
> reported MIP gap is therefore a gap *with respect to the filtered problem*,
> and the honest statement is that the solution is optimal for the restricted
> arc set. Raising `knn` enlarges $A$ and tightens that caveat at the cost of
> solve time.

### 9.2 MustGo adjustment to the available fleet

If the forced bins weigh more than the fleet can carry,

$$
\sum_{i \in \mathcal{F}} S_i \;>\; K^{\max} \cdot Q ,
$$

the model as stated is **infeasible**: constraints 8.1, 8.5 and 8.8 cannot hold
simultaneously. Rather than fail, the code greedily keeps the fullest bins.

Bins are sorted by **fill ratio** $S_i / \mathrm{CAP}_i$ in decreasing order and
accepted while the running total fits in $K^{\max} Q$; the rest are demoted to
optional and recorded with the reason `MG_downgraded_fleet_full`.

Sorting by *ratio* rather than by absolute weight is deliberate: a 40 kg bin at
95 % of capacity is closer to overflowing than a 120 kg bin at 50 %, and
overflow is what the policy is trying to avoid.

Two properties to keep in mind:

* This is a **greedy knapsack**, not an exact one. It does not maximise the
  weight packed into $K^{\max} Q$.
* A demoted bin is not excluded — it merely loses its forcing constraint, and
  the optimiser may still serve it if profitable.

> **This check is about kilograms only.** It does not test whether the forced
> bins can be *reached* inside $K^{\max}$ shifts. See
> [section 10.3](#103-a-feasibility-trap-worth-knowing-about).

### 9.3 Warm start

A nearest-neighbour heuristic builds up to $K^{\max}$ routes and hands the
resulting $(x, g, k)$ to Gurobi as a MIP start. It only accelerates the search;
it does not constrain the solution, and Gurobi discards it if it violates a
constraint.

Two properties are required of it here, and formulation 1's warm start had
neither:

* **It must reach every forced bin.** Walking greedily from one MustGo to the
  next does not work under arc filtering: $\kappa$ keeps each node's nearest
  neighbours, and those are mostly *not* forced, so the walk runs out of
  admissible arcs after a handful of them. The heuristic therefore allows
  optional bins as **stepping stones**, chosen to minimise
  $D_{\text{cur},j} + D_{j,\text{target}}$ where *target* is the nearest pending
  forced bin — closeness to the goal, not cheapness of the hop, which is what
  stops the walk from drifting.
* **It must respect the shift when `enforce` is on.** A start violating the
  constraint just added is rejected, and on a model of this size the solver may
  then find no incumbent at all within the time limit. The walk extends to $j$
  only if it can still serve $j$ and return home:
  $t_{\text{cur}} + \tau_{\text{cur},j} + s + \tau_{j,0} \le H^{\min}$.

On the reference instance the first property lifts forced-bin coverage from
25/100 to 100/100. Without it, a 600 s run with the shift enforced terminated
with no feasible solution found; with it, Gurobi loads a start worth 442.02 €
in under a tenth of a second.

> **Reproducibility note.** Changing the warm start does not change the feasible
> set, but it does change the search path. Runs published before this change
> are therefore not bit-reproducible under the current code, even with the seed
> and thread count pinned. Comparisons that must attribute a difference to one
> parameter should re-run both sides with the same code.

---

## 10. What the working day changes

Everything in this section is measured on the reference instance — 491 bins,
cluster C7, $Q = 4000$ kg, $K^{\max} = 2$, $v = 30$ km/h, $s = 1.5$ min,
$H = 8$ h — solved for 6 h at a 5 % target gap.

### 10.1 The optimum moves

| | `enforce: false` | `enforce: true`, $H = 8$ h |
|---|---|---|
| Bins collected | 317 | 257 |
| Weight | 7 999.3 kg | 7 539.1 kg |
| Distance | 290.37 km | 287.16 km |
| Profit | 1 009.32 € | 937.74 € |
| Longest route | **9.58 h** | **8.00 h** |
| Capacity used | 100.0 % / 99.98 % | 99.7 % / 88.8 % |
| Proven gap | 2.35 % | 11.16 % |

The constraint costs about 7 % of profit and 60 bins. It is not a reporting
change: the previous solution is infeasible under it, so this is a genuinely
different optimum rather than the same routes re-measured.

### 10.2 The binding constraint changes identity

This is the structural result, and it is more interesting than the 7 %.

Without the shift, both routes fill to capacity — the vehicle is the bottleneck.
With the shift, route 1 fills to 99.7 % but **route 2 stops at 88.8 %**, leaving
450 kg of capacity it cannot use because the clock ran out first. Both routes
finish at 479.9 minutes of a 480-minute limit.

The bottleneck therefore moves from **kilograms to minutes**. Any conclusion of
the form "the fleet is too small" has to be re-examined once the working day is
modelled: on this instance the fleet is not too small, the day is too short.

A second, unplanned consequence: the shift-constrained solution is *balanced*
(8.00 h and 8.00 h) where the unconstrained one was not (9.58 h and 8.03 h). The
constraint delivers, for free, a workload-balance property the objective was
never asked to produce — see the corresponding row in
[section 12](#12-what-the-model-still-does-not-capture).

### 10.3 A feasibility trap worth knowing about

Constraint 8.8 forces every bin in $\mathcal{F}$; constraint 8.13 bounds each
route at $H^{\min}$. Together they can be infeasible even when the fleet has
ample capacity, because the check in section 9.2 reasons about **weight** and
the binding resource may be **time**.

A rough sufficient condition for the forced set to be servable is

$$
\underbrace{s\,\lvert \mathcal{F} \rvert}_{\text{service}}
\;+\;
\underbrace{\text{(driving needed to visit } \mathcal{F})}_{\text{route-dependent}}
\;\le\; K^{\max} H^{\min},
$$

whose second term is itself a routing problem and therefore not available before
the solve. On the reference instance the slack is comfortable — 100 forced bins
need 150 min of service against a 960 min budget, leaving 810 min for driving
where roughly 200 suffice — so the trap does not fire. On a denser policy
(a lower $\theta^{MG}$, a longer window $W$) or a shorter shift it can, and the
symptom is an infeasible model rather than a warning.

**The code does not currently check this.** Section 9.2 will report that the
forced set fits in $K^{\max} Q$ kilograms and proceed. Extending that check to
time would require a bound on the routing effort for $\mathcal{F}$ — the warm
start of section 9.3 already computes a feasible tour over the forced set and
would be the natural place to derive one.

---

## 11. Multi-day simulation

For `lookahead.days` consecutive days, the state is carried forward. After the
day's routes are fixed, with $\mathcal{S}$ the set of bins actually served:

$$
\ell_i^{\,t+1} =
\begin{cases}
0 & i \in \mathcal{S} \\[4pt]
\min\!\big(\mathrm{CAP}_i,\; \ell_i^{\,t} + \alpha_i\big) & i \notin \mathcal{S}
\end{cases}
$$

Collection empties a bin completely; everything else accumulates one day of
waste, **clipped at capacity**. The clipping is what makes overflow lossy: waste
arriving at a full bin disappears from the model rather than accumulating, so a
neglected bin stops contributing revenue. That asymmetry is precisely what gives
the lookahead policy its value.

Each day is solved as an independent MILP. There is **no** optimisation across
days: the model is myopic beyond the classification horizon, and the lookahead
sets are the only mechanism carrying future information into today's decision.

> **With `days: 1` the lookahead mechanism is not exercised.** A single day
> forces the $\mathcal{LA}$ bins but never reaches the day on which collecting
> them early would have paid off. On the reference instance the 31 MustGoLA bins
> have a median fill of 80 % while the solver freely chose optional bins down to
> 2 %, so all 31 would have been collected anyway and the classification changed
> nothing. Demonstrating the value of the lookahead requires `days` well above
> the window $W$, comparing $W = 2$ against $W = 1$.

---

## 12. What the model still does not capture

Stated explicitly, because each of these is a real operational concern that the
formulation is silent about. The first two rows of formulation 1's table are now
modelled and have been removed.

| Not modelled | Consequence |
|---|---|
| **Time windows** | Bins can be served at any time inside the shift; no access or noise restrictions. |
| **Crew time as a cost** | Time is a budget, not a price. The model spends the whole shift whenever it pays — see the note in [section 7](#7-objective-function). |
| **Workload balance between drivers** | Not a constraint. It happened to hold under $H = 8$ h because the shift bound both routes; it is not guaranteed. |
| **Breaks and legal rest** | $H$ is a single continuous block. A statutory break inside the shift would reduce the productive budget below $H$. |
| **Variable service time** | $s$ is constant; it does not scale with $\mathrm{Ncont}_i$ or with how full the containers are. |
| **Speed varying by area or hour** | $v$ is one average for the whole round; urban and rural legs are treated alike. |
| **Heterogeneous fleet** | All vehicles share one capacity $Q$, one cost $\Omega$ and one shift $H$. |
| **Multiple depots or intermediate disposal** | One depot; a vehicle never unloads mid-route, so $Q$ binds over the whole day. |
| **Stochastic fill rates** | $\alpha_i$ is a deterministic daily average; day-to-day variance is ignored. |
| **Traffic variability** | $D_{ij}$ is a static snapshot from the ORS profile; $v$ does not vary with congestion. |
| **Overflow penalty** | Waste arriving at a full bin vanishes (section 11); missing a MustGo costs nothing beyond the lost revenue. |

---

## 13. Model size and solution method

For the reference instance — 491 bins, 492 nodes, $\kappa = 25$:

| Quantity | `enforce: false` | `enforce: true` |
|---|---|---|
| Nodes | 492 | 492 |
| Arcs in the complete digraph | 241 572 | 241 572 |
| Arcs after filtering | 16 866 — **7.0 %** kept | 16 866 |
| Binary variables $x$ | 16 866 | 16 866 |
| Binary variables $g$ | 491 | 491 |
| Continuous variables | 33 732 ($y, f$) | **50 598** ($y, f, t$) |
| Constraints | $O(\lvert A \rvert)$ | $O(\lvert A \rvert)$, ~1.5× more |

The filtering is what makes the instance solvable at all: it discards 93 % of
the arcs before Gurobi sees the model.

**The shift constraint is expensive.** It adds a third flow over the same arc
set, growing the presolved model to roughly 67 000 rows and columns. Measured on
the reference instance, the same 6 h budget that proved a 2.35 % gap without it
proved only 11.16 % with it. The cost is not in finding good solutions — the
incumbent climbed steadily from 442 € to 938 € — but in the **dual bound**,
which stalled at 1 042.44 € after 80 minutes and did not move for the remaining
four hours.

That stall is diagnostic rather than mysterious. The log shows ~21 000 simplex
iterations per branch-and-bound node and only 1 483 nodes explored in six hours:
with `node_method: 2` (barrier) each node LP is re-solved from scratch, since
barrier does not warm-start from the parent basis. For runs with the shift
enforced, `node_method: 1` (dual simplex), `mip_focus: 3` (bound rather than
feasibility) and a lower `heuristics` are the parameters to try first.

Solved with Gurobi under `MIP_GAP` and `TIME_LIMIT`. The solver stops at
whichever comes first and always reports the gap it managed to prove, so a
time-limited run still yields a usable solution with a quantified quality bound.
A solution stopped at a non-zero gap is feasible and its objective is a valid
**lower bound** on the optimum — which is the only claim that should be made
about it.

The free *size-limited* Gurobi licence cannot handle a model of this size; an
academic or commercial licence is required.

---

## 14. Equation-to-code map

| Formulation | Code |
|---|---|
| $\mathrm{CAP}_i$, $S_i$, $\alpha_i$ (4.1) | [instance.py](src/vrpp_lookahead/instance.py) — `load_instance` |
| $\tau_{ij}$, $H^{\min}$, route duration (4.2) | [config.py](src/vrpp_lookahead/config.py) — `Shift.travel_min`, `Shift.route_min`, `Shift.max_shift_min` |
| $\mathcal{MG}$, $\mathcal{LA}$ (section 5) | [lookahead.py](src/vrpp_lookahead/lookahead.py) — `lookahead` |
| Arc filtering (9.1) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_arcs` |
| MustGo adjustment (9.2) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_adjust_mustgo` |
| Warm start (9.3) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_warm_start` |
| Variables $x, y, f, g, k$ (section 6) | `solve_vrpp` — `mdl.addVars(...)` |
| Variable $t$ (section 6) | `solve_vrpp` — inside `if sh.enforce:` |
| Objective (section 7) | `solve_vrpp` — `mdl.setObjective(...)` |
| Constraints 8.1 – 8.9 | `solve_vrpp` — the main `mdl.addConstr(...)` block |
| Constraints 8.10 – 8.13 | `solve_vrpp` — the `if sh.enforce:` block |
| Route reconstruction | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_extract_routes` |
| Working day per route, reporting | [reporting.py](src/vrpp_lookahead/reporting.py) — `kpi_per_route` |
| Working day of externally supplied routes | [fixed_routes.py](src/vrpp_lookahead/fixed_routes.py) — `evaluate_fixed_routes` |
| Crew-hour normalised comparison | [scripts/06_shift_analysis.py](scripts/06_shift_analysis.py) |
| State update (section 11) | [simulation.py](src/vrpp_lookahead/simulation.py) — `simulate` |

### A note on the reported classification

In `solve_vrpp` the forcing flag `crit` is initialised from
$\mathcal{MG} \cup \mathcal{LA}$, and the per-node label `kind` is then derived
as *"MustGo if forced, else MustGoLA if originally MustGoLA, else Optional"*.
Since MustGoLA bins are also forced, they end up labelled `MustGo` in the
per-route KPI sheet `3_KPI_Routes` — which is why that sheet can report
`N_MustGoLA = 0` while the lookahead identified 31 of them.

The true split is preserved in `kind_orig` and is what sheets `5_MustGo` and
`6_MustGoLA` use. Any analysis needing the genuine MustGo / MustGoLA breakdown
should read those, or the classification in `1_Lookahead` — not the per-route
counts.

### Reproducing the two formulations

```bash
# Formulation 1 — no working day (shift.enforce: false)
python scripts/03_run_vrpp.py --config config/instance_491_C7_gap1.yaml

# Formulation 2 — 8 h working day enforced
python scripts/03_run_vrpp.py --config config/instance_491_C7_shift8h.yaml

# Compare them on equal crew hours
python scripts/06_shift_analysis.py --config config/instance_491_C7_shift8h.yaml \
    --external "EVOX routes fixed in our model=data/raw/evox_routes_491_C7.yaml" \
    --solution "VRPP no shift=results/491_C7_gap1/Day_01/result_Day_01.xlsx" \
    --solution "VRPP 8 h shift=results/491_C7_shift8h/Day_01/result_Day_01.xlsx"
```
