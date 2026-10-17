# Hand-off: thockin

Prepared by Inheritance from Kubernetes public records as of commit 98d0129cf21. Every line cites the OWNERS file or KEP it comes from. This is a proposal for the people involved, not a decision.

**Scope:** 137 code areas, 107 design decisions, leadership seats: Network.

## Code areas that need a new approver

| Area | Left after departure | Proposed | Why | Patch |
|---|---|---|---|---|
| `pkg/proxy/apis/config` | **nobody** | aojea | already reviews this area (kubernetes/kubernetes/pkg/proxy/apis/config/OWNERS); approves the nearest parent folder; worked with thockin (shared weight 49) | ready |
| `vendor/sigs.k8s.io/randfill` | **nobody** | dims | approves the nearest parent folder; worked with thockin (shared weight 106.5) | ready |
| `pkg/apis/networking` | smarterclayton | aojea | already reviews this area (kubernetes/kubernetes/pkg/apis/networking/OWNERS); worked with thockin (shared weight 49) | ready |
| `pkg/registry/core/service` | aojea | deads2k | approves the nearest parent folder; worked with thockin (shared weight 117.5) | ready |
| `pkg/registry/networking` | aojea | deads2k | approves the nearest parent folder; worked with thockin (shared weight 117.5) | ready |
| `pkg/volume/configmap` | saad-ali | jsafrane | already reviews this area (kubernetes/kubernetes/pkg/volume/configmap/OWNERS); approves the nearest parent folder; worked with thockin (shared weight 85.5) | ready |
| `pkg/volume/downwardapi` | saad-ali | jsafrane | already reviews this area (kubernetes/kubernetes/pkg/volume/downwardapi/OWNERS); approves the nearest parent folder; worked with thockin (shared weight 85.5) | ready |
| `pkg/volume/emptydir` | saad-ali | jsafrane | already reviews this area (kubernetes/kubernetes/pkg/volume/emptydir/OWNERS); approves the nearest parent folder; worked with thockin (shared weight 85.5) | ready |
| `pkg/volume/git_repo` | saad-ali | jsafrane | already reviews this area (kubernetes/kubernetes/pkg/volume/git_repo/OWNERS); approves the nearest parent folder; worked with thockin (shared weight 85.5) | ready |
| `pkg/volume/secret` | saad-ali | jsafrane | already reviews this area (kubernetes/kubernetes/pkg/volume/secret/OWNERS); approves the nearest parent folder; worked with thockin (shared weight 85.5) | ready |

## Decisions with nobody active left who wrote or approved them

- KEP-2447 Make kube-proxy service abstraction optional (kubernetes/enhancements/keps/sig-network/2447-Make-kube-proxy-service-abstraction-optional/kep.yaml)
- KEP-2943 Network Policy status subresource (kubernetes/enhancements/keps/sig-network/2943-networkpolicy-status/kep.yaml)
- KEP-3726 standard-application-protocols (kubernetes/enhancements/keps/sig-network/3726-standard-application-protocols/kep.yaml)
- KEP-265 IPVS Load Balancing Mode in Kubernetes (kubernetes/enhancements/keps/sig-network/265-ipvs-based-load-balancing/kep.yaml)
- KEP-614 SCTP support (kubernetes/enhancements/keps/sig-network/614-SCTP-support/kep.yaml)
- KEP-2079 Allow a Network Policy to contemplate a set of ports in a single rule (kubernetes/enhancements/keps/sig-network/2079-network-policy-port-range/kep.yaml)
- KEP-2450 Remove knowledge of pod cluster CIDR from iptables rules (kubernetes/enhancements/keps/sig-network/2450-Remove-knowledge-of-pod-cluster-CIDR-from-iptables-rules/kep.yaml)
- KEP-2091 Add support for AdminNetworkPolicy resources (kubernetes/enhancements/keps/sig-network/2091-admin-network-policy/kep.yaml)
- KEP-2595 Expanded DNS Configuration (kubernetes/enhancements/keps/sig-network/2595-expanded-dns-config/kep.yaml)
- KEP-3836 Kube-proxy improved ingress connectivity reliability (kubernetes/enhancements/keps/sig-network/3836-kube-proxy-improved-ingress-connectivity-reliability/kep.yaml)
- KEP-1734 Image Promoter (kubernetes/enhancements/keps/sig-release/1734-k8s-image-promoter/kep.yaml)
- KEP-3458 Remove transient node predicates from KCCM's service controller (kubernetes/enhancements/keps/sig-network/3458-remove-transient-node-predicates-from-service-controller/kep.yaml)

## Who already carries this knowledge

- **liggitt**: approves 81 of the same areas; shared decisions: KEP-6313 Network-Isolated Pod Sandboxes, KEP-6164 Eliminating Internal API Types, KEP-5975 Declarative API Definitions
- **deads2k**: approves 69 of the same areas; shared decisions: KEP-6164 Eliminating Internal API Types, KEP-5975 Declarative API Definitions, KEP-5241 Beta Feature Gate Promotion Requirements
- **smarterclayton**: approves 70 of the same areas; shared decisions: KEP-2527 Clarify if/how controllers can use status to track non-observable state, KEP-1672 Tracking Terminating Endpoints in EndpointSlice, KEP-1669 Proxy Terminating Endpoints

## Apply the OWNERS changes

`git apply handoff-thockin.patch` in a kubernetes/kubernetes checkout, then open a PR for the owners to review.

_Answers come from public project records and are attributed to the people in them. Inheritance never imitates a person or speaks in their voice._
