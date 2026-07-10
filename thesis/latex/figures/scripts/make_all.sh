#!/usr/bin/env bash
# Regenerate every data figure (PDFs land in latex/img/).
# Requires: python3 with pandas, numpy, matplotlib; the lag-validation results
# tree at nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/.
set -euo pipefail
cd "$(dirname "$0")"
# fig_geometry (the retired single-figure geometry plot) stays retired; its
# content now lives in the split family: fig_heatmap (windows, completion
# only), fig_geometry_law (bounded two-sided law), fig_knife (r75 pool sweep).
for f in fig_flagship fig_effectiveness fig_heatmap fig_geometry_law \
         fig_knife fig_valley fig_backpressure_loop fig_throttle_trace fig_race \
         fig_cost fig_ladder fig_cpu_io fig_composition \
         fig_gridlock fig_regimes fig_pvalues \
         fig_nexmark fig_nexmark_sweep fig_nexmark_cost; do
  python3 "$f.py"
done
echo "all figures regenerated"
