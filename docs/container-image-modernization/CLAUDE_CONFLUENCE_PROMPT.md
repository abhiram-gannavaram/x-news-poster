# Claude Prompt: Confluence Architecture Proposal

Source: Notion page “Claude Prompt — Create the Confluence Architecture Proposal”

Copy the prompt below into Claude to generate the leadership- and security-ready Confluence architecture proposal.

```text
You are acting as a Principal Solution Architect, Platform Architect, and DevSecOps Architect.

I need you to create a professional, leadership-ready Confluence architecture proposal for a container-image portfolio modernization and ownership-transition project.

This document will be reviewed by Engineering Leadership, my team lead, Platform/DevOps, Security/DevSecOps, JFrog/Artifact Management, Application teams, Observability/New Relic, PKI/Security, Cloud Security, and potentially Architecture Review / Audit stakeholders.

The document must look like a serious enterprise architecture proposal, not a casual technical note.

IMPORTANT WRITING STYLE

Do NOT write as though any decision has already been made.

Avoid wording such as:
- We decided
- We will remove
- Developers must
- Platform will stop
- This is the final architecture
- Starting tomorrow

Use wording such as:
- The proposed direction is
- The working recommendation is
- One option for consideration is
- Subject to Security and Leadership approval
- For stakeholder review
- If approved
- The intent is
- We would like to validate
- This proposal recommends piloting

The tone must be confident, technically strong, collaborative, architecture-driven, and decision-oriented.

Do not criticize the existing implementation or the people who created it. Present the current model as something that may have been appropriate when the portfolio was smaller, but which now creates lifecycle and ownership overhead as the estate has grown.

PROJECT CONTEXT

We currently maintain approximately 63 shared Docker/container images.

The general current flow is:

Vendor/public image
→ internal Dockerfile/build
→ add internal labels or small customization
→ GitHub Actions build
→ Snyk scan
→ publish to JFrog Artifactory
→ developers consume internal image

We also use Wiz for runtime/container visibility.

When vulnerabilities are reported later, Platform/DevOps may receive tickets or requests, investigate the issue, rebuild or patch where possible, re-scan, and republish the image.

This creates recurring maintenance responsibility for vendor software such as AWS runtimes, Microsoft .NET, Debian, Python, Node.js, Java/OpenJDK, Maven, Nginx, Ubuntu, Alpine, and Amazon Linux.

CURRENT IMAGE INVENTORY

Base OS images:
- Alpine (base/rootfs)
- Amazon Linux 2
- Amazon Linux 2023
- Debian Bookworm slim
- Debian Bullseye slim
- Ubuntu 22.04

.NET:
- ASP.NET 10 Alpine
- ASP.NET 8 Jammy
- .NET SDK 8 Jammy

AWS Lambda runtime images:
- Lambda .NET 8
- Lambda Java 11
- Lambda Java 17
- Lambda Java 21 AL2023
- Lambda Node.js 20
- Lambda Node.js 24 AL2023
- Lambda Python 3.10
- Lambda Python 3.11
- Lambda Python 3.12
- Lambda Python 3.13

AWS SAM/build images:
- SAM Lambda Java 11
- SAM Lambda Java 17
- SAM Java 21 AL2023
- SAM Node 24
- other corresponding SAM build variants

Maven:
- Java 8/11/17/21 combinations on AL2 and AL2023

Nginx:
- Nginx Alpine
- Nginx Stable

Node.js:
- Node 22
- Node 24
- Node 26
- some include a corporate/company certificate

OpenJDK:
- Java 8/11/17 across Alpine, AL2, AL2023
- OpenJDK images mainly add the New Relic Java agent

Python:
- Python 3.10–3.14
- Python 3.11.9 slim Bookworm

IMPORTANT PORTFOLIO OBSERVATION

For most images, we do not add meaningful company-specific hardening or platform agents.

Most images are essentially:

Vendor image + labels/metadata → rebuild → republish

The notable exceptions are:
- some Node images add a company/corporate certificate
- OpenJDK images add New Relic

We do NOT generally add major security agents, corporate runtimes, large platform services, substantial OS hardening, or other significant company-specific behavior.

CORE ARCHITECTURAL QUESTION

Does centrally rebuilding third-party images provide enough enterprise value to justify Platform owning their lifecycle and vulnerability management?

The refined proposal is NOT to maintain all 63 images and NOT necessarily to own zero images.

The refined working recommendation is to consider keeping only a very small set of strategic foundational OS base images under Platform ownership while transitioning higher-level runtimes, tools, AWS-managed images, middleware, and application-specific images to vendor consumption through JFrog.

CANDIDATE SMALL PLATFORM-OWNED BASE SET

Potential candidates:
- Alpine
- Amazon Linux 2023
- Ubuntu LTS
- optionally Debian if direct enterprise usage justifies it

Amazon Linux 2 should be treated as a legacy/migration concern rather than a long-term strategic target.

IMPORTANT CHALLENGE TO ADDRESS

Leadership or Security may ask:

“If Alpine, AL2023, and Ubuntu are also vendor images and Platform is not adding much, why should Platform maintain even these?”

The proposal must answer this directly and honestly.

Do NOT claim that keeping these base images is technically mandatory.

The value case for retaining a very small base set is organizational and operational:
- provide a stable internal base-image contract/path for teams that need a generic OS base
- centralize a very small amount of common metadata/labels
- provide a controlled update checkpoint before a new upstream digest becomes company-supported
- provide auditability and rollback history
- provide a location for future genuinely base-level requirements if needed
- reduce the estate from dozens of runtime combinations to only a few low-touch foundational images

Also state clearly:

If Leadership/Security decide that even this thin base layer does not add enough value, those foundational images can also move to direct vendor consumption through JFrog later.

The key principle is:

Platform ownership should exist only where the organization can clearly explain the value added by that ownership.

BASE IMAGE UPDATE WORKFLOW

Do NOT introduce Renovate or another dependency-management platform unless there is a clear requirement.

Keep the update model simple and GitHub Actions-based.

Proposed flow:

Upstream vendor base
→ scheduled or triggered GitHub Action checks upstream tag/digest
→ if digest has not changed, no action
→ if digest changed, workflow creates an update branch/PR
→ candidate image is built
→ Snyk scan runs
→ smoke/validation tests run
→ if checks fail, PR remains blocked and Security/Platform reviews
→ if checks pass, human review/approval is required
→ merge
→ publish the approved base image to JFrog

The important principle is:

new upstream digest → PR → build/scan/test → human approval → publish

NOT:

CVE → Platform manually patches vendor packages → creates a company fork

Suggested controls:
- track exact upstream digest
- no automatic production publication when upstream changes
- successful CI/security checks required
- human approval before merge/publish
- record source tag, source digest, scan result, build reference, and publish date
- preserve previous approved versions for rollback/audit according to policy
- avoid relying only on floating latest tags
- if Security policy fails and no vendor fix exists, route to Security/risk review instead of manually modifying the vendor distribution by default

PROPOSED OWNERSHIP BOUNDARY

Platform may own:
- small supported foundational base-image set
- GitHub Action that checks upstream changes
- build/Snyk/smoke-test gates for those base images
- human-reviewed publication to JFrog
- version/rollback metadata
- reusable labels
- future justified base-level requirements
- reusable CI/CD guardrails

Platform should not normally own:
- Python runtime lifecycle
- Node.js lifecycle
- Java/OpenJDK lifecycle
- .NET lifecycle
- Maven lifecycle
- AWS Lambda lifecycle
- AWS SAM lifecycle
- Nginx lifecycle
- manual patching of unchanged vendor images as the normal operating model

APPLICATION MODEL

If an application genuinely needs a generic OS base, it may use the small Platform-supported base set.

Examples:
- company/alpine
- company/al2023
- company/ubuntu

But language/runtime applications should normally use the vendor runtime through the enterprise JFrog path rather than installing the language manually on top of the company base just to preserve Platform ownership.

Examples:
- Python app → vendor Python image through JFrog
- Node app → vendor Node image through JFrog
- .NET app → Microsoft runtime through JFrog
- Lambda app → AWS Lambda base through JFrog
- Maven/SAM build → vendor build image through JFrog

This is important because we do not want to recreate the current runtime/version/OS matrix.

SECURITY MODEL

Developers are NOT being given uncontrolled public internet access.

Preferred route:

Trusted vendor registry
→ JFrog remote/virtual repository
→ application team

JFrog remains the enterprise distribution/proxy/cache layer.

The final application image remains subject to mandatory Snyk scanning before release.

Flow:

Developer selects vendor/base image
→ application build
→ final application image
→ mandatory Snyk scan
→ Security policy
→ PASS → JFrog release repository → deploy
→ FAIL → app team remediates or Security exception
→ Wiz provides runtime visibility

Important message:

Developer choice of base image does not mean freedom to bypass enterprise security policy.

BUILD-CONTAINER SECURITY

Treat SAM, Maven, .NET SDK, and other build images separately from runtime images.

A final runtime-image scan does not fully cover the risk of a compromised build image that can access:
- source code
- GitHub tokens
- CI secrets
- package credentials
- build outputs

Recommended controls:
- trusted vendor sources
- JFrog proxy/cache
- least-privileged CI credentials
- short-lived secrets where possible
- scoped package tokens
- optional Xray/Curation/scanning if Security requires pre-use inspection

Do not conclude that Platform must maintain its own Maven/SAM images solely because build-container security exists.

XRAY / CURATION / NO-XRAY

Treat ownership and enforcement as separate decisions.

Decision 1:
Should Platform continue rebuilding unchanged vendor images?

Decision 2:
What security controls should apply to vendor consumption?

If Xray/Curation is available and Security wants it, use it as defense in depth.

If it is not available, use:
- trusted JFrog proxy/cache
- application ownership
- mandatory final Snyk security gate
- Wiz runtime visibility

Do not make Xray a prerequisite for the ownership transition.

COMPANY LABELS

Most images are rebuilt only to add labels.

Move labels to the standard final application build workflow.

Examples:
- company
- application
- source repository
- build ID
- git commit
- owner
- version

Labels alone should not justify a separate runtime image.

CORPORATE CERTIFICATE / NODE

Do not maintain company-node-22, company-node-24, company-node-26 only because they contain a corporate CA certificate.

Propose a reusable Enterprise Trust Bundle integration:

Corporate CA
→ controlled/versioned artifact in JFrog
→ tested Dockerfile integration snippets or reusable build helper
→ application image

Provide tested patterns for:
- Debian/Ubuntu-family containers
- Alpine
- Java truststores if required

Suggested ownership:
- PKI/Security owns CA lifecycle
- Platform owns the integration pattern
- Application team consumes it when required

The certificate requirement should not force Platform to own Node.js lifecycle.

NEW RELIC / OPENJDK

OpenJDK images currently create a large matrix because New Relic is bundled into them.

Evaluate two options:

Option A — New Relic Kubernetes APM auto-attach where technically and operationally supported.

Option B — application-level standard New Relic integration:
Vendor Java runtime + versioned New Relic Java agent + standard configuration → application image

Platform/Observability may own:
- standard New Relic version/source
- configuration guidance
- reusable example
- integration snippet

Application team owns Java runtime choice and lifecycle.

Do not remove OpenJDK shared images until Observability/Security validate an equivalent replacement path.

PHASED TRANSITION

Do NOT propose a big-bang shutdown.

Phase 0 — Architecture & ownership alignment
- final inventory
- active consumer inventory
- classify all images
- confirm small strategic base-image decision
- validate JFrog capabilities
- check Xray/Curation availability
- define Security requirements
- define Snyk policy
- agree exception process
- agree RACI
- agree migration/deprecation rules

Decision gate:
Leadership + Security + JFrog approval to proceed.

Phase 1 — AWS Lambda & SAM
- identify current consumers
- validate AWS Public ECR through JFrog remote/proxy/cache
- pilot representative Lambda images
- separately test one SAM build image
- validate final Snyk gate
- validate build-container controls
- validate Xray/Curation if available
- document old path → new path
- migrate pilot teams
- monitor
- announce deprecation
- make old repositories/build pipelines read-only
- retire only after migration

Do not delete immediately.

Phase 2 — Establish small Platform base-image model
For the foundational base images Leadership/Security choose to retain:
- implement GitHub Actions upstream-change detection
- raise PR automatically when digest changes
- build candidate
- run Snyk
- run smoke tests
- require human approval
- merge
- publish to JFrog
- retain source digest and rollback metadata

Phase 3 — Move remaining label-only runtime/tool images to vendor consumption
Examples:
- Python
- Maven
- .NET
- Nginx
- other label-only runtime/tool images

Move company labels into final application-build automation.

Phase 4 — Decouple thin company customizations
4A corporate certificate
4B New Relic

Validate equivalent integration before retiring the corresponding shared images.

Phase 5 — Controlled retirement
Replacement validated
→ consumers identified
→ migration guide
→ pilot
→ general migration
→ old repos read-only
→ usage monitoring
→ no new versions
→ retire workflows/support
→ archive/delete based on policy

UPDATED IMAGE-FAMILY DIRECTION

Create a clear table with this working proposal:

Alpine → candidate for small Platform-owned base set
Amazon Linux 2023 → candidate for small Platform-owned base set
Ubuntu LTS → candidate for small Platform-owned base set
Debian → keep only if direct enterprise usage/value justifies it, otherwise vendor via JFrog
Amazon Linux 2 → legacy/migration path
AWS Lambda → vendor via JFrog; Phase 1 deprecation of internal copies
AWS SAM → vendor via JFrog with build-container controls
Python → vendor via JFrog
Node → vendor via JFrog; decouple certificate
OpenJDK → vendor/application ownership after New Relic is decoupled
Maven → vendor via JFrog
.NET → vendor via JFrog
Nginx → vendor via JFrog

RACI

Create a professional responsibility table.

Suggested ownership:

Vendor:
- upstream image maintenance
- vendor security fixes
- vendor release lifecycle

Application Team:
- base/runtime choice
- Dockerfile
- app dependencies
- runtime upgrades
- final image
- rebuild/redeploy

Platform:
- small strategic base-image pipelines if approved
- GitHub Actions guardrails
- standard labels
- JFrog consumption pattern enablement
- trust-bundle integration pattern
- New Relic enablement pattern
- migration documentation/support

Security:
- vulnerability policy
- block criteria
- exceptions/risk acceptance
- unsupported software/EOL policy

JFrog/Artifact Management:
- remotes
- virtual repos
- proxy/cache
- auth/access
- logging/audit

PKI:
- CA certificate lifecycle

Observability:
- New Relic standard/instrumentation approach

Cloud Security:
- Wiz runtime visibility

Manual patching of unchanged vendor images:
- not proposed as normal Platform responsibility

QUESTIONS FOR SECURITY

Include a dedicated section with at least:
1. Is mandatory final-image scanning acceptable as the primary release control for runtime images?
2. Is pre-download/base-image scanning required?
3. If yes, should Xray/Curation or another approved control provide it?
4. How should Critical vulnerabilities with no vendor fix be handled?
5. What is the exception/risk-acceptance process?
6. What is the expiry policy?
7. Are build containers subject to additional controls?
8. Should CI enforce unsupported/EOL runtime policies?
9. What response is required when a new CVE is discovered after deployment?
10. Does Security support keeping only a small strategic base-image set instead of the full runtime matrix?
11. If Security does not see value in even those base images, is full vendor consumption through JFrog acceptable?

QUESTIONS FOR JFROG

Include:
- AWS Public ECR proxy support
- Docker Hub proxy
- Microsoft MCR
- other vendor registries
- recommended remote/virtual design
- authentication
- caching
- mutable tags
- manifest refresh
- multi-architecture support
- audit logs
- Xray availability
- Curation availability
- licensing
- security controls

DEVELOPER EXPERIENCE

Do not present the model as “we are transferring work to developers.”

Position it as:

Application teams receive clearer ownership of the software they deploy while Platform provides a paved road.

Developers should receive:
- JFrog vendor paths
- example Dockerfiles
- migration mappings
- standard labels
- trust-bundle pattern
- New Relic pattern
- reusable CI workflow
- automatic Snyk scan
- clear remediation guidance
- migration support window

RISK REGISTER

Include a professional risk/mitigation table with at least:
- developer chooses vulnerable base
- Security perceives reduced governance
- JFrog upstream unavailable
- mutable tags
- vendor removes image
- build-container compromise
- Snyk unavailable
- no vendor fix
- certificate integration problems
- New Relic integration problems
- legacy consumers do not migrate
- multi-architecture differences
- vendor rate limiting
- hard-coded old image names
- auditability concerns
- rollback requirements
- exception debt
- small base-image ownership may still provide limited value

For the last risk, mitigation should be:
review the value periodically and allow those base images to move to direct vendor consumption through JFrog if the organizational value is not demonstrated.

SUCCESS CRITERIA

Include measurable criteria:
- no developer loses a supported path without replacement
- Lambda/SAM path validated
- JFrog proxy validated
- final Snyk gate validated
- Security control model approved
- RACI approved
- small base-image update workflow proven if that option is accepted
- corporate CA pattern validated
- New Relic pattern validated
- migration documentation created
- application owners identified
- legacy repos moved read-only only after migration
- vendor CVE tickets no longer automatically become Platform manual-patching work
- only images with explicit organizational value remain centrally owned

LEADERSHIP DECISION REQUEST

Do NOT ask leadership to approve removal of all 63 images immediately.

Ask for staged decisions:

Decision A:
Approve the ownership principle: central ownership should exist only where Platform adds explicit value.

Decision B:
Authorize a controlled Phase 1 Lambda/SAM pilot through JFrog.

Decision C:
Review whether a small strategic base-image set (Alpine, AL2023, Ubuntu LTS, optionally Debian) provides sufficient organizational value to retain.

Decision D:
If the pilot succeeds, authorize progressive migration of label-only runtime/tool images and decoupling of certificate/New Relic requirements.

CONFLUENCE PAGE STRUCTURE

Create a polished Confluence page with sections approximately like:
1. Executive Summary
2. Purpose
3. Current State
4. Current Portfolio
5. Problem Statement
6. Why Revisit the Existing Model
7. Architecture Principles
8. Options Considered
9. Refined Working Recommendation
10. Why Keep Any Platform Base Images?
11. Small Base-Image Update Architecture
12. Current Architecture Diagram
13. Target Architecture Diagram
14. Security Model
15. Build-Container Security
16. Xray/Curation/No-Xray Branch
17. Ownership/RACI
18. Phase 0
19. Phase 1 Lambda/SAM
20. Phase 2 Small Base Set
21. Phase 3 Label-Only Images
22. Phase 4A Certificate
23. Phase 4B New Relic
24. Phase 5 Retirement
25. Image-Family Recommendation Matrix
26. Developer Migration Experience
27. Security Questions
28. JFrog Questions
29. Observability/PKI Questions
30. Risks & Mitigations
31. Acceptance Criteria
32. Timeline
33. Leadership Decisions Required
34. Suggested Review-Meeting Narrative
35. Recommended Next Actions
36. Appendix / references

DIAGRAMS

Create Mermaid diagrams if Confluence supports Mermaid. Otherwise provide equivalent draw.io/Gliffy-ready diagram descriptions.

Required diagrams:

1. Current state:
Vendor → internal build → labels/customization → Snyk → JFrog → developer
and CVE → ticket → Platform → rebuild

2. Refined target state:
Platform → small supported base set
Vendor registries → JFrog proxy/cache → runtimes/tools/Lambda/middleware
Both paths → application build → final Snyk → release → deploy → Wiz

3. Small base-image update workflow:
upstream digest change → GitHub Action → PR → build → Snyk/tests → human approval → merge → JFrog publish

4. Phase roadmap:
Phase 0 → Phase 1 Lambda/SAM → Phase 2 base set → Phase 3 label-only → Phase 4 customizations → Phase 5 retirement

5. Security branch:
Xray/Curation available? yes → defense in depth; no → trusted proxy + final Snyk; both → same ownership model

6. Ownership model:
Vendor / Platform / Application / Security / JFrog / Observability / PKI

CONFLUENCE FORMATTING

Use:
- info panels
- note panels
- decision panels
- tables
- expandable technical sections
- status lozenges where useful
- architecture diagrams
- phased roadmap
- RACI
- risk matrix
- decision log

Avoid giant walls of text.

The first screen of the page should be understandable to a senior leader in approximately 2–3 minutes.

PRESENTATION STRATEGY

The narrative should be:

1. There is a real scaling/ownership issue.
2. We are not proposing a sudden shutdown.
3. Security is not being removed.
4. We start with Lambda/SAM because the vendor path is clear.
5. We may keep a small strategic OS foundation as a pragmatic enterprise compromise.
6. We openly acknowledge that even those bases are vendor software and ask stakeholders whether the value justifies keeping them.
7. We eliminate the runtime/tool matrix.
8. We decouple legitimate company requirements such as CA certificates and New Relic.
9. We retire only after migration.

KEY MESSAGES

Use these as prominent callouts:

“Proposed direction: reduce centrally maintained container images to only those where Platform ownership provides explicit organizational value.”

“Stop duplicating vendor lifecycle work while retaining enterprise security guardrails.”

“The proposed change is an ownership correction, not a reduction in container security.”

“Keeping a small Platform base-image set is a pragmatic option for review, not a technical requirement.”

TIMELINE

Keep the architecture validation practical and relatively aggressive.

Suggested target:
Days 1–3: inventory, consumer mapping, Security/JFrog alignment, base-set decision
Days 4–7: Lambda/SAM PoC and JFrog/Snyk validation
Days 8–10: pilot migration and documentation
Days 11–13: small base-image workflow PoC and label-only image mapping
Days 14–15: CA/New Relic PoCs and final deprecation proposal

Target approximately 15 business days for architecture validation, PoCs, stakeholder decision package, and migration design.

Do NOT claim all applications or repositories will be fully migrated in 15 days.

FINAL OUTPUT

Before writing the final page, critically review the architecture from four perspectives:
1. Security Architect
2. Platform/DevOps Architect
3. Application Developer
4. Engineering Leadership

Challenge weak assumptions and improve them.

Then create the complete Confluence-ready page.

If you have access to Confluence, create a new Confluence page using native Confluence formatting/macros rather than only returning Markdown.

If you do not have Confluence write access, return the full Confluence-ready content.

Finally provide:
- executive summary
- full Confluence page
- architecture diagrams
- RACI
- phased roadmap
- risk matrix
- Security questions
- leadership decision request
- suggested talking points for the review meeting

Do not make any architectural decision appear already approved. Everything must remain a proposal for stakeholder review and decision.
```
