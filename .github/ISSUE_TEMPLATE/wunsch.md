---
name: Feature request
about: Something Dive on Wide should be able to do
labels: enhancement
---

**What do you want to do that you cannot do today?**
Describe the situation, not the solution — the best fix is often a different
one than the one that comes to mind first.

**Would it need a dependency?**
The core is pure standard library. Features that need a library live behind an
optional switch that degrades honestly when it is absent. If yours needs one,
say which and what happens without it.

**Can it be turned off?**
Anything that touches the user's hardware, network or files must be optional
and revocable.
