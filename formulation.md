# Mathematical formulation — VRPP + Lookahead

Complete specification of the model implemented in
[src/vrpp_lookahead/vrpp.py](src/vrpp_lookahead/vrpp.py),
[src/vrpp_lookahead/lookahead.py](src/vrpp_lookahead/lookahead.py) and
[src/vrpp_lookahead/instance.py](src/vrpp_lookahead/instance.py).

This document describes what the code actually solves, not an idealised version
of it. Every equation carries a pointer to the lines that build it, and
[section 10](#10-what-the-model-does-not-capture) states plainly what the model
leaves out.

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
10. [What the model does not capture](#10-what-the-model-does-not-capture)
11. [Multi-day simulation](#11-multi-day-simulation)
12. [Model size and solution method](#12-model-size-and-solution-method)
13. [Equation-to-code map](#13-equation-to-code-map)

---

## 1. Problem statement

A fleet of identical vehicles based at a single depot collects waste from a set
of bins spread over a road network. Unlike a classical VRP, **not every bin has
to be visited**: each bin carries a *profit* proportional to the waste it holds,
and the operator is free to skip a bin whose collection does not pay for the
detour. This is the **Vehicle Routing Problem with Profits (VRPP)**, in its
*prize-collecting* variant — the objective trades revenue against travel cost
rather than minimising distance under full-coverage constraints.

Two things make this instance specific:

* **Mandatory subset.** Bins that would overflow before the next visit are
  forced into the solution regardless of profitability. These come from the
  *lookahead* classification of [section 5](#5-lookahead-classification).
* **Fleet-size decision.** The number of routes actually used, `k`, is itself a
  variable bounded by `MAX_ROUTES`, and each vehicle used costs a fixed `OMEGA`.

The result is a single mixed-integer linear program solved with Gurobi, one per
simulated day.

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

### Per-bin data, read from the attributes file

| Symbol | Column | Unit | Meaning |
|---|---|---|---|
| $\mathrm{Ncont}_i$ | `Ncont` | — | number of containers standing at point $i$ |
| $\mathrm{Vol}_i$ | `Vol_cont` | m³ | volume of **one** container at point $i$ |
| $\mathrm{Vkg}_i$ | `Vol_kg` | kg | waste currently held at point $i$ |
| $a_i$ | `ai` | %/day | daily fill rate, as a percentage of point capacity |

### Network data, from the OpenRouteService matrix

| Symbol | Unit | Meaning |
|---|---|---|
| $D_{ij}$ | km | road distance from $i$ to $j$ |
| $T_{ij}$ | min | road travel time from $i$ to $j$ |

$D$ is **asymmetric** in general ($D_{ij} \neq D_{ji}$), because it comes from a
real directed road network with one-way streets. The formulation is stated over
a directed graph for exactly this reason.

### Model parameters, from the YAML file

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

---

## 4. Derived quantities

Computed once, in
[instance.py](src/vrpp_lookahead/instance.py), and used everywhere afterwards:

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
> [section 13](#13-equation-to-code-map).

---

## 6. Decision variables

| Variable | Domain | Meaning |
|---|---|---|
| $x_{ij}$ | $\{0,1\}$, $(i,j) \in A$ | arc $(i,j)$ is traversed |
| $g_i$ | $\{0,1\}$, $i \in N_R$ | bin $i$ is collected |
| $y_{ij}$ | $\mathbb{R}_{\ge 0}$, $(i,j) \in A$ | load carried along arc $(i,j)$ [kg] |
| $f_{ij}$ | $\mathbb{R}_{\ge 0}$, $(i,j) \in A$ | unit flow along arc $(i,j)$ — connectivity only |
| $k$ | $\mathbb{Z}$, $0 \le k \le K^{\max}$ | number of routes used |

Two distinct flow variables are carried on purpose:

* $y_{ij}$ is **physical**: kilograms actually in the truck on that arc. It
  enforces the capacity $Q$.
* $f_{ij}$ is **fictitious**: one unit of an abstract commodity sent from the
  depot to every served bin. It has no physical meaning and exists solely to
  forbid subtours. Merging the two would be possible but would weaken the
  linear relaxation, because a bin with $S_i = 0$ would carry no physical load
  and could then sit on a disconnected cycle.

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

> The travel time $T_{ij}$ **does not enter the objective**. It is measured and
> reported, but it is not optimised and not constrained.

---

## 8. Constraints

### 8.1 Fleet size

$$
k \;\le\; K^{\max}
$$

Also imposed as a variable bound. This is the constraint that binds hardest in
practice: with a small $K^{\max}$ the fleet capacity $K^{\max} \cdot Q$ may be
unable to hold all forced bins, which triggers the adjustment of
[section 9.2](#92-mustgo-adjustment-to-the-available-fleet).

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

When the adjustment fires, the run is no longer solving the policy as stated:
it is solving a relaxed policy the fleet can actually deliver. The diagnostics
of `03_run_vrpp.py --diagnose-only` warn about this **before** committing to a
long solve.

### 9.3 Warm start

A nearest-neighbour heuristic builds up to $K^{\max}$ routes over the forced
bins only, respecting $Q$ and the filtered arc set, and the resulting
$(x, g, k)$ is handed to Gurobi as a MIP start. It only accelerates the search;
it does not constrain the solution. Gurobi rejects it silently if it violates a
constraint.

---

## 10. What the model does not capture

Stated explicitly, because each of these is a real operational concern that the
formulation is silent about:

| Not modelled | Consequence |
|---|---|
| **Route duration / shift limits** | $T_{ij}$ is reported but never constrained. A route may exceed a legal working day. |
| **Time windows** | Bins can be served at any time; no access restrictions. |
| **Workload balance between drivers** | The objective is joint profit, so the optimiser will happily produce one 170 km route and one 136 km route. |
| **Service (stop) time** | Only travel time is measured; the time spent emptying a container is absent. |
| **Heterogeneous fleet** | All vehicles share a single capacity $Q$ and cost $\Omega$. |
| **Multiple depots or intermediate disposal** | One depot; a vehicle never unloads mid-route. |
| **Stochastic fill rates** | $\alpha_i$ is a deterministic daily average; day-to-day variance is ignored. |
| **Traffic variability** | $D_{ij}$ and $T_{ij}$ are a static snapshot from the ORS profile. |

Adding route duration or driver balance means new constraints, not new
post-processing — they change which solution is optimal.

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

---

## 12. Model size and solution method

For the reference instance — 491 bins, 492 nodes, $\kappa = 25$:

| Quantity | Order of magnitude |
|---|---|
| Nodes | 492 |
| Arcs in the complete digraph | 241 572 |
| Arcs after filtering | 16 866 — **7.0 %** kept |
| Binary variables $x$ | 16 866 |
| Binary variables $g$ | 491 |
| Continuous variables $y, f$ | 33 732 |
| Constraints | $O(\lvert A \rvert)$ |

The filtering is what makes the instance solvable at all: it discards 93 % of
the arcs before Gurobi sees the model.

Solved with Gurobi under `MIP_GAP` and `TIME_LIMIT`. The solver stops at
whichever comes first and always reports the gap it managed to prove, so a
time-limited run still yields a usable solution with a quantified quality bound.

Empirically, on this instance, reaching a 5 % gap takes minutes while tightening
to 1 % takes substantially longer — the usual shape for a prize-collecting
routing problem, where the bound improves slowly because the LP relaxation can
fractionally serve many low-profit bins.

The free *size-limited* Gurobi licence cannot handle a model of this size; an
academic or commercial licence is required.

---

## 13. Equation-to-code map

| Formulation | Code |
|---|---|
| $\mathrm{CAP}_i$, $S_i$, $\alpha_i$ | [instance.py](src/vrpp_lookahead/instance.py) — `load_instance` |
| $\mathcal{MG}$, $\mathcal{LA}$ (section 5) | [lookahead.py](src/vrpp_lookahead/lookahead.py) — `lookahead` |
| Arc filtering (9.1) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_arcs` |
| MustGo adjustment (9.2) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_adjust_mustgo` |
| Warm start (9.3) | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_warm_start` |
| Variables (section 6) | `solve_vrpp` — `mdl.addVars(...)` for `x`, `y`, `f`, `g`, `k` |
| Objective (section 7) | `solve_vrpp` — `mdl.setObjective(...)` |
| Constraints 8.1 – 8.9 | `solve_vrpp` — the `mdl.addConstr(...)` block |
| Route reconstruction | [vrpp.py](src/vrpp_lookahead/vrpp.py) — `_extract_routes` |
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
