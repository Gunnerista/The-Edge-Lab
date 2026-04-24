"""
DNP model training script.
Blocked by: feature_store.py (next module).

When ready:
 1. Load historical games from data/historical/player_games.parquet
 2. Generate 15 features per player-per-game
 3. Target: minutes_played >= 5 (binary)
 4. Train XGBoost (max_depth=5, lr=0.05, n_est=500, early stop)
 5. Calibrate with Platt on validation set
 6. Validate: AUC >= 0.80, Brier <= 0.12, ECE <= 0.04
 7. Save pickle to data/models/dnp_model_v1.pkl
"""

# TODO: implement after feature_store.py lands.
