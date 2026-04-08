"""
Stage 3: Surrogate Model Training — Neural Network (MLP)
========================================================
Reads the combined training data, scales the features, and trains
a Multilayer Perceptron (MLP) to mimic GeoStudio's FS results.

This script can run safely while Stage 2 is still processing.
"""

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.neural_network import MLPRegressor
from sklearn.metrics import r2_score, mean_absolute_error
import joblib

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
DATA_CSV    = r"E:\Github\MCP_Geostudio\training\training_data_combined.csv"
MODEL_FILE  = r"E:\Github\MCP_Geostudio\training\surrogate_fs_model.pkl"
SCALER_FILE = r"E:\Github\MCP_Geostudio\training\scaler.pkl"

# Features to use for training
FEATURES = [
    # Soil Strength
    "c_awyc_psf", "phi_awyc_deg", "c_wyc_psf", "phi_wyc_deg",
    # Rainfall / Antecedent
    "return_period_yr", "storm_duration_days", "total_depth_in",
    "API_7d_mm", "API_14d_mm", "API_21d_mm", "API_30d_mm"
]

TARGET = "min_FS"

# ---------------------------------------------------------------------------
# Load and Clean Data
# ---------------------------------------------------------------------------
def load_data(csv_path):
    print(f"Loading data from {csv_path}...")
    # Read CSV, skipping metadata comments starting with '#'
    df = pd.read_csv(csv_path, comment='#')
    
    # Filter only converged results
    initial_len = len(df)
    df = df[df['converged'] == 1].copy()
    
    # Drop rows where target is N/A (should be handled by converged check)
    df = df.dropna(subset=[TARGET])
    
    print(f"  Total rows found: {initial_len}")
    print(f"  Valid samples for training: {len(df)}")
    
    return df

# ---------------------------------------------------------------------------
# Training Pipeline
# ---------------------------------------------------------------------------
def train():
    if not os.path.exists(DATA_CSV):
        print(f"ERROR: Data file not found at {DATA_CSV}")
        return

    df = load_data(DATA_CSV)
    if len(df) < 50:
        print("ERROR: Not enough data yet (minimum 50 samples recommended).")
        return

    X = df[FEATURES].values
    y = df[TARGET].values

    # 1. Split data (80% train, 20% test)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # 2. Scale features (CRITICAL for Neural Networks)
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled  = scaler.transform(X_test)

    # 3. Define the Neural Network (MLP)
    # Hidden layers: 100 neurons -> 50 neurons -> 25 neurons
    print("\nTraining Neural Network (MLP)...")
    model = MLPRegressor(
        hidden_layer_sizes=(100, 50, 25),
        activation='relu',
        solver='adam',
        alpha=0.0001,
        batch_size='auto',
        learning_rate_init=0.001,
        max_iter=1000,
        random_state=42,
        verbose=False,
        early_stopping=True,
        validation_fraction=0.1
    )

    # 4. Fit
    model.fit(X_train_scaled, y_train)

    # 5. Evaluate
    y_pred = model.predict(X_test_scaled)
    r2 = r2_score(y_test, y_pred)
    mae = mean_absolute_error(y_test, y_pred)

    print("-" * 40)
    print(f"MODEL PERFORMANCE (Test Set)")
    print(f"  R² Score : {r2:.4f}")
    print(f"  MAE      : {mae:.4f}")
    print("-" * 40)

    # 6. Save Model and Scaler
    print(f"\nSaving model to {MODEL_FILE}")
    joblib.dump(model, MODEL_FILE)
    print(f"Saving scaler to {SCALER_FILE}")
    joblib.dump(scaler, SCALER_FILE)

    # 7. Visualize Loss Curve
    plt.figure(figsize=(8, 5))
    plt.plot(model.loss_curve_)
    plt.title("Neural Network Training Loss")
    plt.xlabel("Iterations")
    plt.ylabel("Loss")
    plt.grid(True)
    plt.savefig(os.path.join(os.path.dirname(DATA_CSV), "training_loss.png"))
    print("\nTraining complete! Loss curve saved to training/training_loss.png")

if __name__ == "__main__":
    train()
