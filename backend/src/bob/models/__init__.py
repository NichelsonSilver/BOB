"""Modelos probabilísticos — PUROS, sin I/O. Cobertura objetivo >= 90%.

  markov.py      — detector de régimen (heredado; baseline y fallback del HMM)
  labeling.py    — triple-barrier, targets y pesos por unicidad
  validation.py  — walk-forward purgado con embargo
  forecast.py    — P(TP antes que SL) calibrada, volatilidad, cono conformal
  baselines.py   — RandomWalk, EWMA, GARCH(1,1), HAR-RV
  metrics.py     — Brier, ECE, AUC, QLIKE, Winkler, Diebold-Mariano
  experiment.py  — orquesta el walk-forward completo
  report.py      — renderiza el reporte a texto
  projection.py  — KPI 2: TP/SL por sigma, EV neto, leverage y liquidación
  hmm.py         — HMM gaussiano de regímenes, n por BIC/ICL, filtrado causal
"""
