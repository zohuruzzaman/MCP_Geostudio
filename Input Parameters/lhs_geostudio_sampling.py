"""
Latin Hypercube Sampling for GeoStudio FEM Simulations
=======================================================
Generates parameter combinations for slope stability analysis
Based on sensor data from s2_sensor.csv

Author: Generated for FEM-ML hybrid modeling workflow
"""

import numpy as np
import pandas as pd
from scipy.stats import qmc
import matplotlib.pyplot as plt

# Set random seed for reproducibility
np.random.seed(42)

# =============================================================================
# DEFINE PARAMETER RANGES
# =============================================================================

# Based on your sensor data analysis
parameters = {
    # RAINFALL PARAMETERS (SEEP/W Boundary Conditions)
    'rainfall_intensity': {
        'min': 10,
        'max': 200,
        'unit': 'mm/day',
        'description': 'Daily rainfall intensity'
    },
    'rainfall_duration': {
        'min': 1,
        'max': 21,
        'unit': 'days',
        'description': 'Storm duration'
    },
    'antecedent_7day_precip': {
        'min': 0,
        'max': 250,
        'unit': 'mm',
        'description': '7-day antecedent rainfall (from sensor: 0-222)'
    },
    
    # INITIAL CONDITIONS (SEEP/W)
    'initial_moisture_5ft': {
        'min': 0.30,
        'max': 0.50,
        'unit': 'VWC',
        'description': 'Initial volumetric water content at 5ft'
    },
    'initial_moisture_10ft': {
        'min': 0.40,
        'max': 0.55,
        'unit': 'VWC',
        'description': 'Initial volumetric water content at 10ft'
    },
    'initial_suction': {
        'min': 5,
        'max': 15,
        'unit': 'kPa',
        'description': 'Initial matric suction (absolute value)'
    },
    
    # HYDRAULIC PROPERTIES (SEEP/W)
    'ksat': {
        'min': 1e-7,
        'max': 1e-5,
        'unit': 'm/s',
        'description': 'Saturated hydraulic conductivity',
        'log_scale': True
    },
    'air_entry_value': {
        'min': 1,
        'max': 10,
        'unit': 'kPa',
        'description': 'Air entry value for SWCC'
    },
    
    # SOIL STRENGTH PARAMETERS (SLOPE/W)
    'cohesion': {
        'min': 0,
        'max': 30,
        'unit': 'kPa',
        'description': 'Effective cohesion'
    },
    'friction_angle': {
        'min': 15,
        'max': 35,
        'unit': 'degrees',
        'description': 'Effective friction angle'
    },
    'unit_weight': {
        'min': 17,
        'max': 21,
        'unit': 'kN/m³',
        'description': 'Soil unit weight'
    },
    
    # GEOMETRY
    'slope_angle': {
        'min': 20,
        'max': 40,
        'unit': 'degrees',
        'description': 'Slope inclination'
    },
    'slope_height': {
        'min': 5,
        'max': 20,
        'unit': 'm',
        'description': 'Slope height'
    },
    'water_table_depth': {
        'min': 0,
        'max': 5,
        'unit': 'm below surface',
        'description': 'Initial water table depth'
    }
}

# =============================================================================
# LATIN HYPERCUBE SAMPLING FUNCTION
# =============================================================================

def generate_lhs_samples(parameters, n_samples=500, seed=42):
    """
    Generate Latin Hypercube Samples for GeoStudio parameters
    
    Parameters:
    -----------
    parameters : dict
        Dictionary of parameter ranges
    n_samples : int
        Number of samples to generate
    seed : int
        Random seed for reproducibility
    
    Returns:
    --------
    pd.DataFrame : DataFrame with sampled parameter values
    """
    
    param_names = list(parameters.keys())
    n_params = len(param_names)
    
    # Create Latin Hypercube Sampler
    sampler = qmc.LatinHypercube(d=n_params, seed=seed)
    
    # Generate samples in [0, 1] space
    samples_unit = sampler.random(n=n_samples)
    
    # Scale samples to actual parameter ranges
    samples_scaled = np.zeros_like(samples_unit)
    
    for i, param_name in enumerate(param_names):
        param = parameters[param_name]
        
        if param.get('log_scale', False):
            # Log-scale sampling for parameters like ksat
            log_min = np.log10(param['min'])
            log_max = np.log10(param['max'])
            samples_scaled[:, i] = 10 ** (log_min + samples_unit[:, i] * (log_max - log_min))
        else:
            # Linear scaling
            samples_scaled[:, i] = param['min'] + samples_unit[:, i] * (param['max'] - param['min'])
    
    # Create DataFrame
    df = pd.DataFrame(samples_scaled, columns=param_names)
    
    # Round appropriate columns
    df['rainfall_duration'] = df['rainfall_duration'].round().astype(int)
    df['antecedent_7day_precip'] = df['antecedent_7day_precip'].round(1)
    df['rainfall_intensity'] = df['rainfall_intensity'].round(1)
    df['cohesion'] = df['cohesion'].round(1)
    df['friction_angle'] = df['friction_angle'].round(1)
    df['unit_weight'] = df['unit_weight'].round(1)
    df['slope_angle'] = df['slope_angle'].round(1)
    df['slope_height'] = df['slope_height'].round(1)
    df['water_table_depth'] = df['water_table_depth'].round(2)
    df['initial_moisture_5ft'] = df['initial_moisture_5ft'].round(3)
    df['initial_moisture_10ft'] = df['initial_moisture_10ft'].round(3)
    df['initial_suction'] = df['initial_suction'].round(1)
    df['air_entry_value'] = df['air_entry_value'].round(1)
    
    # Add simulation ID
    df.insert(0, 'sim_id', range(1, n_samples + 1))
    
    return df


def add_derived_parameters(df):
    """
    Add derived/calculated parameters useful for GeoStudio
    """
    # Total rainfall for the event
    df['total_rainfall'] = df['rainfall_intensity'] * df['rainfall_duration']
    
    # Combined rainfall (antecedent + event)
    df['combined_rainfall'] = df['antecedent_7day_precip'] + df['total_rainfall']
    
    # Approximate saturated unit weight
    df['unit_weight_sat'] = df['unit_weight'] + 2  # rough approximation
    
    # Initial estimate of stability (simple infinite slope)
    # FoS ≈ (c' + γ·z·cos²β·tanφ') / (γ·z·sinβ·cosβ)
    # Simplified relative indicator
    df['stability_indicator'] = (
        df['cohesion'] + 
        df['unit_weight'] * np.tan(np.radians(df['friction_angle']))
    ) / (df['unit_weight'] * np.tan(np.radians(df['slope_angle'])))
    
    return df


def categorize_scenarios(df):
    """
    Categorize scenarios for easier filtering
    """
    # Rainfall intensity category
    df['rainfall_category'] = pd.cut(
        df['rainfall_intensity'],
        bins=[0, 30, 60, 100, 200],
        labels=['Light', 'Moderate', 'Heavy', 'Extreme']
    )
    
    # Strength category
    df['strength_category'] = pd.cut(
        df['cohesion'] + df['friction_angle'],
        bins=[0, 25, 40, 65],
        labels=['Low', 'Medium', 'High']
    )
    
    # Wetness category (antecedent)
    df['antecedent_category'] = pd.cut(
        df['antecedent_7day_precip'],
        bins=[-1, 50, 120, 250],
        labels=['Dry', 'Moderate', 'Wet']
    )
    
    return df


def check_lhs_coverage(df, parameters):
    """
    Verify LHS coverage of parameter space
    """
    print("\n" + "="*70)
    print("LHS COVERAGE CHECK")
    print("="*70)
    print(f"\n{'Parameter':<25} {'Min Sample':>12} {'Max Sample':>12} {'Target Min':>12} {'Target Max':>12}")
    print("-"*70)
    
    for param in parameters.keys():
        if param in df.columns:
            print(f"{param:<25} {df[param].min():>12.4g} {df[param].max():>12.4g} "
                  f"{parameters[param]['min']:>12.4g} {parameters[param]['max']:>12.4g}")


def plot_lhs_distributions(df, parameters, save_path=None):
    """
    Visualize the LHS distributions
    """
    param_cols = [p for p in parameters.keys() if p in df.columns]
    n_params = len(param_cols)
    n_cols = 4
    n_rows = int(np.ceil(n_params / n_cols))
    
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(16, 3*n_rows))
    axes = axes.flatten()
    
    for i, param in enumerate(param_cols):
        axes[i].hist(df[param], bins=30, edgecolor='black', alpha=0.7)
        axes[i].set_title(f"{param}\n({parameters[param]['unit']})")
        axes[i].axvline(df[param].mean(), color='red', linestyle='--', label='Mean')
        axes[i].set_xlabel('Value')
        axes[i].set_ylabel('Frequency')
    
    # Hide empty subplots
    for j in range(i+1, len(axes)):
        axes[j].set_visible(False)
    
    plt.suptitle('Latin Hypercube Sample Distributions', fontsize=14, y=1.02)
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.show()


def plot_parameter_correlations(df, parameters, save_path=None):
    """
    Check that LHS maintains low correlations between parameters
    """
    param_cols = [p for p in parameters.keys() if p in df.columns]
    
    corr_matrix = df[param_cols].corr()
    
    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(corr_matrix, cmap='RdBu_r', vmin=-1, vmax=1)
    
    ax.set_xticks(range(len(param_cols)))
    ax.set_yticks(range(len(param_cols)))
    ax.set_xticklabels(param_cols, rotation=45, ha='right')
    ax.set_yticklabels(param_cols)
    
    plt.colorbar(im, label='Correlation')
    plt.title('Parameter Correlations (should be near zero for good LHS)')
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.show()
    
    # Print max off-diagonal correlation
    np.fill_diagonal(corr_matrix.values, 0)
    max_corr = np.abs(corr_matrix.values).max()
    print(f"\nMaximum off-diagonal correlation: {max_corr:.4f}")
    print("(Good LHS should have max correlation < 0.1)")


# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    
    # Number of simulations to generate
    N_SAMPLES = 500  # Adjust as needed (500-1000 recommended)
    
    print("="*70)
    print("LATIN HYPERCUBE SAMPLING FOR GEOSTUDIO FEM SIMULATIONS")
    print("="*70)
    print(f"\nGenerating {N_SAMPLES} parameter combinations...")
    
    # Generate LHS samples
    df_samples = generate_lhs_samples(parameters, n_samples=N_SAMPLES, seed=42)
    
    # Add derived parameters
    df_samples = add_derived_parameters(df_samples)
    
    # Categorize scenarios
    df_samples = categorize_scenarios(df_samples)
    
    # Check coverage
    check_lhs_coverage(df_samples, parameters)
    
    # Print summary statistics
    print("\n" + "="*70)
    print("SAMPLE SUMMARY STATISTICS")
    print("="*70)
    print(df_samples.describe().round(3))
    
    # Print category distributions
    print("\n" + "="*70)
    print("SCENARIO CATEGORY DISTRIBUTIONS")
    print("="*70)
    print("\nRainfall Categories:")
    print(df_samples['rainfall_category'].value_counts())
    print("\nStrength Categories:")
    print(df_samples['strength_category'].value_counts())
    print("\nAntecedent Moisture Categories:")
    print(df_samples['antecedent_category'].value_counts())
    
    # Save to CSV
    output_file = 'geostudio_lhs_parameters.csv'
    df_samples.to_csv(output_file, index=False)
    print(f"\n✓ Saved {N_SAMPLES} parameter combinations to '{output_file}'")
    
    # Create parameter reference table
    print("\n" + "="*70)
    print("PARAMETER REFERENCE TABLE")
    print("="*70)
    print(f"\n{'Parameter':<25} {'Min':>10} {'Max':>10} {'Unit':<15} Description")
    print("-"*90)
    for name, info in parameters.items():
        print(f"{name:<25} {info['min']:>10.4g} {info['max']:>10.4g} {info['unit']:<15} {info['description']}")
    
    # Plot distributions
    print("\n" + "="*70)
    print("GENERATING VISUALIZATIONS...")
    print("="*70)
    
    plot_lhs_distributions(df_samples, parameters, save_path='lhs_distributions.png')
    plot_parameter_correlations(df_samples, parameters, save_path='lhs_correlations.png')
    
    # Show first 10 samples
    print("\n" + "="*70)
    print("FIRST 10 SAMPLE COMBINATIONS")
    print("="*70)
    display_cols = ['sim_id', 'rainfall_intensity', 'rainfall_duration', 'cohesion', 
                    'friction_angle', 'ksat', 'slope_angle', 'rainfall_category', 'strength_category']
    print(df_samples[display_cols].head(10).to_string(index=False))
    
    print("\n" + "="*70)
    print("DONE! Ready for GeoStudio simulations.")
    print("="*70)
