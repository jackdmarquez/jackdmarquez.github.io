---
title: "Application of graph alignment techniques for identifying sources of non-determinism in MPI simulations"
collection: publications
permalink: "/publication/2026-01-31-application-of-graph-alignment-techniques-for-identifying-sources-of-non-determinism-in-mpi-simulations"
date: 2026-01-31
venue: "The International Journal of High Performance Computing Applications"
paperurl: "https://doi.org/10.1177/10943420251398118"
citation: "Dhroov Pandey, Jack Marquez, Michela Taufer, Sanjukta Bhowmick. (2026). Application of graph alignment techniques for identifying sources of non-determinism in MPI simulations. The International Journal of High Performance Computing Applications. doi:10.1177/10943420251398118"
excerpt: "Scientific high performance computing (HPC) applications employ asynchronous executions of MPI calls to improve scalability and performance. The asynchronous calls can lead to non-determinism (ND) in execution, particularly for large exascale simulations. In order to ensure reproducibility and facilitate error detection, it is imperative to identify the sources of non-determinism. Message ND that occurs when the order in which a process sends or receives MPI communication, or executes MPI calls varies across different runs of the same application. We model the MPI calls in the execution as an event graph. The regions of dissimilarity between two event graphs indicate the sources of non-determinism in the MPI calls. Thus by aligning the nodes of the event graphs, we can identify sources of ND. We show that traditional alignment techniques such as NetAlign and learning methodologies such as Graph Autoencoders are not able to align graphs with high accuracy due to the nearly regular degree and large diameter of event graphs. Therefore, we propose a meta graph heuristic that exploits structural properties of event graphs, by combining the set of nodes representing sequences of MPI calls within the same processor as a meta node. We align the meta graphs formed from these meta nodes, and then align the individual nodes within the meta nodes. Our results over three different MPI applications highlight that our meta graph heuristic has better accuracy and scales to large graphs compared to network alignment and graph auto encder methods."
orcid_put_code: "204260735"
orcid_path: "/0000-0002-2673-3507/work/204260735"
doi: "10.1177/10943420251398118"
source: "orcid-bot"
---

Abstract

Scientific high performance computing (HPC) applications employ asynchronous executions of MPI calls to improve scalability and performance. The asynchronous calls can lead to non-determinism (ND) in execution, particularly for large exascale simulations. In order to ensure reproducibility and facilitate error detection, it is imperative to identify the sources of non-determinism. Message ND that occurs when the order in which a process sends or receives MPI communication, or executes MPI calls varies across different runs of the same application. We model the MPI calls in the execution as an event graph. The regions of dissimilarity between two event graphs indicate the sources of non-determinism in the MPI calls. Thus by aligning the nodes of the event graphs, we can identify sources of ND. We show that traditional alignment techniques such as NetAlign and learning methodologies such as Graph Autoencoders are not able to align graphs with high accuracy due to the nearly regular degree and large diameter of event graphs. Therefore, we propose a meta graph heuristic that exploits structural properties of event graphs, by combining the set of nodes representing sequences of MPI calls within the same processor as a meta node. We align the meta graphs formed from these meta nodes, and then align the individual nodes within the meta nodes. Our results over three different MPI applications highlight that our meta graph heuristic has better accuracy and scales to large graphs compared to network alignment and graph auto encder methods.

Links

- [DOI](https://doi.org/10.1177/10943420251398118)

_Generated from ORCID/Crossref metadata by orcid-bot._
