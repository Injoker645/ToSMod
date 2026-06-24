# Ethical Considerations — Method Write-up

> Last changed: 2026-04-23 15:29 UTC — added change-tracking header convention for ongoing thesis-method updates.

**Status**: 📝 Under review — consult supervisor before finalising  
**Thesis integration**: 📝 Not yet added — target §3.1 (Ethical Framework) or standalone ethics appendix  
**Key contacts**: Mikael [ethics advisor], thesis supervisor  
**Relevant legislation**: Swedish Ethical Review Act (2003:460); EU Digital Services Act (DSA) Art. 40

---

## Overview

This write-up documents the ethical framework for the thesis project, covering three interlocking questions: (1) whether formal ethical review is required under Swedish law, (2) what that means for the publication status of the work, and (3) how the collection of publicly available social media data — including data collected in ways that may conflict with a platform's terms of service — is justified under EU law.

---

## 1. Swedish Ethical Review Act — Applicability to Student Theses

Under the *Lag (2003:460) om etikprövning av forskning som avser människor* (the Swedish Ethical Review Act), research involving personal data is generally subject to mandatory ethical review before data collection can begin. However, the Act contains an explicit exemption for work conducted **strictly as part of studies**, meaning student thesis projects at Swedish universities are not required to undergo formal review by an ethics board.

Under this exemption, ethical responsibility for the student's conduct rests with the **thesis supervisor and subject reviewer**, not the student or the university's ethics board directly.

**Practical consequence for this project**: Formal ethical review is not required as a precondition for data collection or analysis in the thesis as submitted.

### Important caveat — publication

The exemption carries a significant condition: work completed under it **cannot subsequently be published as research**. If any output of this thesis is intended to be developed into a peer-reviewed publication (journal article, conference paper, or similar), the project must be re-evaluated under the following criteria before that publication proceeds:

1. **Special categories of personal data**: Does the data include information that falls under the GDPR's "special categories" — health, political opinions, religion, sexual orientation, etc.? Online comments discussing mental health, body image, or identity-based harassment (all of which are likely in this corpus) could fall within this scope.
2. **Risk of harm to participants**: Does the research methodology pose a risk of harm to the individuals whose data is being processed — e.g., through re-identification or amplification of harmful content?

If either condition is met and a publication is planned, a formal ethics application is required. As of 2025, this costs **5 000 SEK** and takes approximately **one month** to process. This cost should not be borne by the student; the decision to apply requires active involvement of the supervisor, who must be a co-applicant or sponsor.

> **Action item**: Discuss publication intent with supervisor before the thesis defence. If a publication is planned, initiate the ethics application in parallel with the thesis submission — do not wait until after graduation.

---

## 2. Data Subjects and Sensitivity of the Corpus

The comments collected in this project are public posts made by Instagram and TikTok users. Under the GDPR, publicly posted content still constitutes personal data if it is linked to an identifiable individual. Key considerations:

- **Author usernames and IDs** are pseudonymised (HMAC-SHA256 hashed with a private salt) before any analysis or storage in the project database. No plaintext usernames are retained in the analytical corpus.
- **Comment content** may incidentally reveal sensitive information about commenters — e.g., disclosures of mental health conditions, political views, religious beliefs, or sexual orientation, particularly given the subject matter of the collected posts (content moderation, online harassment, body image).
- The project does not target any specific individual; data is collected at the post level with the comment section as the unit of analysis. No effort is made to aggregate or profile individual users across posts.
- Collected data is stored locally and on university-affiliated systems only. It is not shared with third parties or used for any purpose beyond the thesis analysis.

---

## 3. Platform Terms of Service and the DSA

### The legal status of scraping for research

Two platforms in this project — TikTok and Instagram — have Terms of Service that prohibit automated or programmatic data collection by users without explicit permission. This creates an apparent conflict between the methodology and the platforms' contractual terms.

The relevant legal instrument for resolving this conflict within the EU context is **Article 40 of the Digital Services Act (DSA)**, specifically paragraph 12.

**DSA Article 40(12)** establishes that researchers affiliated with not-for-profit organisations (including universities) who meet defined independence and data security conditions are entitled to collect publicly accessible data from Very Large Online Platforms (VLOPs) — including by scraping — for the purpose of studying systemic risks in the EU. Crucially, the European Commission has ruled that a platform's contractual prohibition on independent data access (i.e., a ToS clause banning scraping) is *"in direct contradiction with Art. 40.12"* and cannot be enforced against qualifying researchers (European Commission Decision in proceedings against X, 2025).

Both TikTok and Meta (Instagram) are designated as VLOPs under the DSA, and both are subject to Article 40 obligations.

**This project's position under Article 40(12)**: The data collected is limited to publicly visible content (posts and comments accessible without a private account or special access). The research addresses online harmful speech — a topic directly related to "dissemination of illegal content" and "negative effects for the exercise of fundamental rights," both of which are enumerated systemic risks under DSA Article 34(1). The researcher is affiliated with a university and the work is conducted for non-commercial academic purposes.

These conditions mean Article 40(12) provides a substantive legal basis for the data collection methodology, even where platform ToS would otherwise prohibit it. This framing should be cited explicitly in the thesis methodology chapter.

> **Note**: Article 40(12) does not remove all obligations. It does not permit collection of non-public data, does not override GDPR data minimisation requirements, and requires that data security standards are maintained. All three conditions are met by the pseudonymisation pipeline and local-only storage in this project.

### Data minimisation and pseudonymisation

As directed by the ethics advisor, the following data minimisation measures are in place:

| Measure | Implementation |
|---|---|
| No private account data | Collection restricted to publicly visible posts and comments only |
| Author pseudonymisation | HMAC-SHA256 applied to usernames and platform IDs before storage |
| No cross-platform identity linking | The hash salt differs by platform; the same person cannot be linked across TikTok and Instagram datasets |
| No retention of raw identifiers | The pseudonymised DB is the primary record; raw collection files are stored in `data/raw/` but not used in analysis |
| Scope limitation | Only comment text and minimal metadata needed for analysis (date, engagement count, reply structure) are retained |

---

## 4. Supervisor Involvement

Per the ethics advisor's guidance, the following decisions **require active supervisor involvement** and should not be made unilaterally:

- Any decision to submit for formal ethical review (and the associated 5 000 SEK cost).
- Any decision to pursue publication, which triggers the review requirement.
- Any expansion of data collection scope that would involve private accounts, direct messages, or demographic targeting of specific user groups.

---

## References

- *Lag (2003:460) om etikprövning av forskning som avser människor*, www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/lag-2003460-om-etikprovning-av-forskning-som_sfs-2003-460/
- Leerssen et al. (2025), "Using the DSA to Study Platforms," *Verfassungsblog*, verfassungsblog.de/dsa-platforms-digital-services-act/
- European Commission Decision in proceedings against X regarding DSA Article 40 compliance (2025), as discussed in: techpolicy.press/what-the-x-fine-reveals-about-data-access-under-article-40-of-the-digital-services-act
- Lorenz-Spreen et al. (2025), "Research Opportunities and Challenges of the EU's Digital Services Act," *Communications of the ACM* (preprint), synosys.github.io/publication/phillip-2025-digital-service-act/
- Alieva et al. (2025 / ICWSM 2026), "The Great Data Standoff: Researchers vs. Platforms Under the Digital Services Act," arxiv.org/html/2505.01122v2
