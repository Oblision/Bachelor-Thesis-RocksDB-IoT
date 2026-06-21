#!/usr/bin/env bash
# Regenerate every data figure (PDFs land in latex/img/).
# Requires: python3 with pandas, numpy, matplotlib; the lag-validation results
# tree at nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/.
set -euo pipefail
cd "$(dirname "$0")"
for f in fig_flagship fig_effectiveness fig_heatmap fig_geometry fig_race \
         fig_cost fig_ladder fig_cpu_io fig_composition \
         fig_gridlock fig_regimes \
         fig_nexmark fig_nexmark_sweep fig_nexmark_cost; do
  python3 "$f.py"
done
echo "all figures regenerated"
