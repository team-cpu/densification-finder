# Local follow-up — 9 September 2026

Not pushed: the user explicitly deferred the next production push.

Changes:
- Format legacy Swiss phone values when rendering the owner field, board contact line and contact CSV, without rewriting stored records.
- Rank all screening result types by absolute additional floor area descending, as in the supplied HTML. Stable ties preserve source order. This intentionally changes the top-result selection from relative/interleaved ranking.
- Show unavailable email, 2FA and shared-calculation services as disabled/off controls. Preserve stored preferences without claiming operational enforcement. Rename pending invitation retry to Erneut vormerken, matching its actual local-record behavior.

Remaining scope:
- User confirmed there is no email/auth provider. Actual delivery, personal accounts, enforced 2FA and account-based sharing require implementation and service provisioning; these are not completed by disabling the controls.
- Current parcel data has no verified ES II noise-limit violation assessment. Keep the transport filter truthful; do not rename it as a noise filter or infer violations from a noise sensitivity class. A noise exclusion needs an authoritative data source and assessment rule.
- No production data was changed and no deployment was performed.

Verification: organisation tests passed (18 tests and 7 subtests). Ranking, app and acquisition regression results recorded in the task response. git diff --check passed. This pass did not re-run manual browser interaction checks.
