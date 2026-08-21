# ARM Reference Manuals

Kept here for optimization work against the AYN Thor's actual silicon rather than
ARM64 in the abstract.

## Why these specific guides

The Thor's Snapdragon 8 Gen 2 is a heterogeneous 1+4+3 complex, and every core in
it has its own optimization guide here:

| Cluster | Core | Guide |
| --- | --- | --- |
| Prime x1 | Cortex-X3 | `cortex-x3-software-optimization-guide.pdf` |
| Performance x2 | Cortex-A715 | `cortex-a715-software-optimization-guide.pdf` |
| Performance x2 | Cortex-A710 | `cortex-a710-software-optimization-guide.pdf` |
| Efficiency x3 | Cortex-A510 | `cortex-a510-software-optimization-guide.pdf` |

That heterogeneity is the point. These cores do **not** share a pipeline model —
instruction latencies, issue widths and NEON throughput differ enough that "fast on
the X3" and "fast on the A510" are different questions. The Thor Lite is a
Snapdragon 865 (Cortex-A77/A55) and matches none of these, so anything tuned from
them needs a sanity check there.

The optimization guides are what you want for scheduling and instruction-selection
questions. The architecture reference manual is for semantics — what an instruction
actually does.

## The architecture reference manual is not tracked

`arm-architecture-reference-manual-a-profile.pdf` is ~66MB and is deliberately
gitignored: committing it would put 66MB into every clone of this repo forever.

Download it from ARM's documentation site (search "ARM Architecture Reference
Manual for A-profile architecture", DDI 0487) and drop it in this folder. Nothing
breaks without it; it is a lookup reference, not a build input.

## Source

Copied from the sibling `cemu-thor-experiment` checkout, which collected them for
the same device.
