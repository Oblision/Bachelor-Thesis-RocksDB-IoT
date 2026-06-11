---
name: user-solar-battery
description: "Workstation is powered by a home solar + battery setup, so always-on services have effectively zero marginal energy cost"
metadata: 
  node_type: memory
  type: user
  originSessionId: 4320a1f4-7fa6-4af0-a75f-d71cb4e8ff41
---

The user's workstation (`oblision-MS-7C75`, MSI desktop) runs on a household solar + battery system. Standard electricity-cost tradeoffs (e.g. "keeping a desktop awake 24/7 costs ~€10–15/month") do not meaningfully apply when proposing always-on services, scheduled jobs, or sleep-disable settings.

**How to apply:** When weighing "keep it running" vs. "let it sleep / scale to zero" recommendations, drop the energy-cost argument from the comparison or note it as ~zero. Lean toward always-on solutions if they're simpler. Confirmed 2026-05-14 when the user picked `systemctl mask suspend.target` for the self-hosted runner specifically citing their solar + battery setup.
