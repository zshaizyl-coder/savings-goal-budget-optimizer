# Stage 10 experiments

Run all core experiments, statistics, figures, metadata, and the report:

```powershell
python -m experiments.stage10.run_all
```

To regenerate tables and figures from existing core CSV files:

```powershell
python -m experiments.stage10.run_all --reuse-results
```

The frozen scenario configuration is `configs/stage10.json`. Outputs contain
only anonymized aggregates and synthetic results; transaction-level files are
not read by this package.
