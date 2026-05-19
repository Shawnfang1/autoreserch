"""
实验2：1D-CNN vs 传统方法对比实验
=================================
在同一数据集上对比三种方法的精度、低浓度性能和抗噪声能力。

方法:
    1. 直接吸收法 (DA) - Voigt 轮廓拟合
    2. PLS 偏最小二乘回归
    3. 1D-CNN (TinyTDLASNet)

生成时间: 2026-05-19
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats
from scipy.optimize import curve_fit
from sklearn.cross_decomposition import PLSRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import torch
import torch.nn as nn
import os
import sys
import json
import warnings
warnings.filterwarnings('ignore')

# 导入模型定义
sys.path.insert(0, os.path.dirname(__file__))
from importlib import import_module


# ============================================================
# 1. 模型定义（与 02_1D_CNN_Model.py 一致）
# ============================================================

class TinyTDLASNet(nn.Module):
    """轻量级 1D-CNN（与 02_1D_CNN_Model.py 一致）"""
    def __init__(self, in_channels=1, seq_len=512):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv1d(in_channels=1, out_channels=8, kernel_size=7, stride=4, padding=3),
            nn.ReLU(),
            nn.Conv1d(in_channels=8, out_channels=16, kernel_size=5, stride=4, padding=2),
            nn.ReLU(),
            nn.Conv1d(in_channels=16, out_channels=32, kernel_size=3, stride=2, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.regressor = nn.Sequential(
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        return self.regressor(x).squeeze(-1)


# ============================================================
# 2. 直接吸收法 (DA)
# ============================================================

def voigt_profile(x, amplitude, center, gamma_L, gamma_G):
    """伪 Voigt 轮廓近似"""
    # Lorentzian
    L = gamma_L**2 / ((x - center)**2 + gamma_L**2)
    # Gaussian
    G = np.exp(-0.5 * ((x - center) / gamma_G)**2)
    # 混合
    eta = gamma_L / (gamma_L + gamma_G)  # 混合因子
    return amplitude * (eta * L + (1 - eta) * G)


def direct_absorption_concentration(signal, x_axis=None):
    """
    直接吸收法：提取 2f 信号特征 -> 浓度。

    简化实现：用信号的峰峰值（2f 幅值）作为浓度的代理。
    实际 TDLAS 中，2f 幅值与浓度成正比。
    """
    # 2f 信号幅值 = 峰峰值
    return np.max(signal) - np.min(signal)


# ============================================================
# 3. PLS 方法
# ============================================================

class PLSMethod:
    """偏最小二乘回归"""

    def __init__(self, n_components=5):
        self.n_components = n_components
        self.model = PLSRegression(n_components=n_components)
        self.scaler = StandardScaler()
        self.fitted = False

    def fit(self, X, y):
        """
        训练 PLS 模型。

        参数:
            X: shape (n_samples, n_features) - 2f 信号
            y: shape (n_samples,) - 浓度
        """
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled, y)
        self.fitted = True
        return self

    def predict(self, X):
        """预测浓度"""
        if not self.fitted:
            raise RuntimeError("模型未训练")
        X_scaled = self.scaler.transform(X)
        y_pred = self.model.predict(X_scaled)
        return y_pred.flatten()


# ============================================================
# 4. 数据加载
# ============================================================

def load_dataset(csv_path='tdlas_dataset/tdlas_2f_dataset.csv'):
    """加载 TDLAS 数据集"""
    if not os.path.exists(csv_path):
        print(f"数据集不存在: {csv_path}")
        print("请先运行 01_TDLAS_Simulation.py 生成数据集")
        return None, None

    df = pd.read_csv(csv_path)
    # 格式: concentration_ppm, snr_db, signal_0, ..., signal_511
    y = df['concentration_ppm'].values
    signal_cols = [c for c in df.columns if c.startswith('signal_')]
    X = df[signal_cols].values
    return X, y


def add_noise(X, snr_db):
    """添加高斯噪声"""
    signal_power = np.mean(X ** 2, axis=1, keepdims=True)
    noise_power = signal_power / (10 ** (snr_db / 10))
    noise = np.random.normal(0, np.sqrt(noise_power), X.shape)
    return X + noise


# ============================================================
# 5. 评估函数
# ============================================================

def evaluate_methods(X_train, y_train, X_test, y_test, model_path='best_tdlas_model.pth'):
    """
    在同一数据集上评估三种方法。

    返回: dict with results for each method
    """
    results = {}

    # --- 方法 1: 直接吸收法 (DA) ---
    print("\n[1/3] 直接吸收法 (DA)...")
    y_pred_da = np.array([direct_absorption_concentration(x) for x in X_test])
    # DA 输出的是幅值，需要线性映射到浓度
    slope_da, intercept_da, _, _, _ = stats.linregress(y_pred_da, y_test)
    y_pred_da_mapped = slope_da * y_pred_da + intercept_da

    results['DA'] = {
        'y_pred': y_pred_da_mapped,
        'mae': mean_absolute_error(y_test, y_pred_da_mapped),
        'rmse': np.sqrt(mean_squared_error(y_test, y_pred_da_mapped)),
        'r2': r2_score(y_test, y_pred_da_mapped),
    }
    print(f"  MAE={results['DA']['mae']:.2f}, R2={results['DA']['r2']:.4f}")

    # --- 方法 2: PLS ---
    print("[2/3] PLS 偏最小二乘...")
    pls = PLSMethod(n_components=min(10, X_train.shape[1]))
    pls.fit(X_train, y_train)
    y_pred_pls = pls.predict(X_test)

    results['PLS'] = {
        'y_pred': y_pred_pls,
        'mae': mean_absolute_error(y_test, y_pred_pls),
        'rmse': np.sqrt(mean_squared_error(y_test, y_pred_pls)),
        'r2': r2_score(y_test, y_pred_pls),
    }
    print(f"  MAE={results['PLS']['mae']:.2f}, R2={results['PLS']['r2']:.4f}")

    # --- 方法 3: 1D-CNN ---
    print("[3/3] 1D-CNN...")
    device = torch.device('cpu')
    model = TinyTDLASNet()

    # 预处理
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    y_max = max(y_train.max(), y_test.max())
    y_train_norm = y_train / y_max
    y_test_norm = y_test / y_max

    if os.path.exists(model_path):
        model.load_state_dict(torch.load(model_path, map_location=device))
    else:
        print(f"  模型文件不存在，训练新模型...")

    # 训练模型（如果需要）
    model.train()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.MSELoss()

    X_tensor_train = torch.FloatTensor(X_train_scaled).unsqueeze(1)
    y_tensor_train = torch.FloatTensor(y_train_norm)

    for epoch in range(50):
        optimizer.zero_grad()
        output = model(X_tensor_train)
        loss = criterion(output, y_tensor_train)
        loss.backward()
        optimizer.step()

    # 预测
    model.eval()
    with torch.no_grad():
        X_tensor_test = torch.FloatTensor(X_test_scaled).unsqueeze(1)
        y_pred_cnn_norm = model(X_tensor_test).numpy()
        y_pred_cnn = y_pred_cnn_norm * y_max

    results['CNN'] = {
        'y_pred': y_pred_cnn,
        'mae': mean_absolute_error(y_test, y_pred_cnn),
        'rmse': np.sqrt(mean_squared_error(y_test, y_pred_cnn)),
        'r2': r2_score(y_test, y_pred_cnn),
    }
    print(f"  MAE={results['CNN']['mae']:.2f}, R2={results['CNN']['r2']:.4f}")

    return results


def evaluate_low_concentration(X_test, y_test, results, threshold=100):
    """评估低浓度区间 (<threshold ppm) 的性能"""
    low_mask = y_test < threshold
    if np.sum(low_mask) < 5:
        print(f"  低浓度样本不足 ({np.sum(low_mask)} 个)")
        return {}

    y_test_low = y_test[low_mask]
    low_results = {}
    for method, res in results.items():
        y_pred_low = res['y_pred'][low_mask]
        low_results[method] = {
            'mae': mean_absolute_error(y_test_low, y_pred_low),
            'rmse': np.sqrt(mean_squared_error(y_test_low, y_pred_low)),
            'r2': r2_score(y_test_low, y_pred_low),
        }
    return low_results


def evaluate_noise_robustness(X_train, y_train, X_test, y_test, model_path='best_tdlas_model.pth',
                               snr_levels=[40, 30, 20, 10]):
    """评估抗噪声能力"""
    noise_results = {method: {'snr': [], 'mae': [], 'r2': []}
                     for method in ['DA', 'PLS', 'CNN']}

    # 预处理
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    y_max = max(y_train.max(), y_test.max())

    # 训练 PLS
    pls = PLSMethod(n_components=10)
    pls.fit(X_train, y_train)

    # 训练 CNN
    device = torch.device('cpu')
    model = TinyTDLASNet()
    model.train()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.MSELoss()
    X_tensor_train = torch.FloatTensor(X_train_scaled).unsqueeze(1)
    y_tensor_train = torch.FloatTensor(y_train / y_max)
    for epoch in range(50):
        optimizer.zero_grad()
        output = model(X_tensor_train)
        loss = criterion(output, y_tensor_train)
        loss.backward()
        optimizer.step()
    model.eval()

    for snr in snr_levels:
        X_noisy = add_noise(X_test, snr)
        X_noisy_scaled = scaler.transform(X_noisy)

        # DA
        y_pred_da = np.array([direct_absorption_concentration(x) for x in X_noisy])
        slope_da, intercept_da, _, _, _ = stats.linregress(y_pred_da, y_test)
        y_pred_da_mapped = slope_da * y_pred_da + intercept_da
        noise_results['DA']['snr'].append(snr)
        noise_results['DA']['mae'].append(mean_absolute_error(y_test, y_pred_da_mapped))
        noise_results['DA']['r2'].append(r2_score(y_test, y_pred_da_mapped))

        # PLS
        y_pred_pls = pls.predict(X_noisy)
        noise_results['PLS']['snr'].append(snr)
        noise_results['PLS']['mae'].append(mean_absolute_error(y_test, y_pred_pls))
        noise_results['PLS']['r2'].append(r2_score(y_test, y_pred_pls))

        # CNN
        with torch.no_grad():
            X_tensor = torch.FloatTensor(X_noisy_scaled).unsqueeze(1)
            y_pred_cnn = model(X_tensor).numpy() * y_max
        noise_results['CNN']['snr'].append(snr)
        noise_results['CNN']['mae'].append(mean_absolute_error(y_test, y_pred_cnn))
        noise_results['CNN']['r2'].append(r2_score(y_test, y_pred_cnn))

    return noise_results


# ============================================================
# 6. 可视化
# ============================================================

def plot_comparison(results, y_test, output_dir='experiment_data'):
    """绘制方法对比图"""
    os.makedirs(output_dir, exist_ok=True)

    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.sans-serif': ['Arial', 'DejaVu Sans'],
        'font.size': 10,
        'axes.linewidth': 0.8,
        'axes.spines.right': False,
        'axes.spines.top': False,
        'figure.dpi': 150,
    })

    colors = {'DA': '#2E86AB', 'PLS': '#F6AE2D', 'CNN': '#E84855'}
    labels = {'DA': 'Direct Absorption', 'PLS': 'PLS Regression', 'CNN': '1D-CNN (Ours)'}

    # 图1：散点对比
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, method in zip(axes, ['DA', 'PLS', 'CNN']):
        y_pred = results[method]['y_pred']
        ax.scatter(y_test, y_pred, c=colors[method], alpha=0.5, s=20)
        ax.plot([0, max(y_test)], [0, max(y_test)], 'k--', linewidth=1)
        ax.set_xlabel('True Concentration (ppm)')
        ax.set_ylabel('Predicted Concentration (ppm)')
        ax.set_title(f'{labels[method]}\nMAE={results[method]["mae"]:.1f}, R²={results[method]["r2"]:.4f}')

    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, 'method_comparison_scatter.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'method_comparison_scatter.pdf'), bbox_inches='tight')

    # 图2：指标柱状图
    fig2, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 5))

    methods = ['DA', 'PLS', 'CNN']
    mae_vals = [results[m]['mae'] for m in methods]
    r2_vals = [results[m]['r2'] for m in methods]

    bars1 = ax1.bar([labels[m] for m in methods], mae_vals,
                    color=[colors[m] for m in methods], alpha=0.8)
    ax1.set_ylabel('MAE (ppm)')
    ax1.set_title('Mean Absolute Error')
    for bar, val in zip(bars1, mae_vals):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                 f'{val:.1f}', ha='center', va='bottom', fontsize=9)

    bars2 = ax2.bar([labels[m] for m in methods], r2_vals,
                    color=[colors[m] for m in methods], alpha=0.8)
    ax2.set_ylabel('R²')
    ax2.set_title('R² Score')
    ax2.set_ylim(min(r2_vals) - 0.01, 1.0)
    for bar, val in zip(bars2, r2_vals):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.001,
                 f'{val:.4f}', ha='center', va='bottom', fontsize=9)

    plt.tight_layout()
    fig2.savefig(os.path.join(output_dir, 'method_comparison_bars.png'), dpi=600, bbox_inches='tight')
    fig2.savefig(os.path.join(output_dir, 'method_comparison_bars.pdf'), bbox_inches='tight')

    plt.close('all')
    print("方法对比图已保存")


def plot_noise_robustness(noise_results, output_dir='experiment_data'):
    """绘制抗噪声能力对比"""
    os.makedirs(output_dir, exist_ok=True)

    colors = {'DA': '#2E86AB', 'PLS': '#F6AE2D', 'CNN': '#E84855'}
    labels = {'DA': 'Direct Absorption', 'PLS': 'PLS Regression', 'CNN': '1D-CNN (Ours)'}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    for method in ['DA', 'PLS', 'CNN']:
        if noise_results[method]['snr']:
            ax1.plot(noise_results[method]['snr'], noise_results[method]['mae'],
                     '-o', color=colors[method], label=labels[method], linewidth=2, markersize=6)
            ax2.plot(noise_results[method]['snr'], noise_results[method]['r2'],
                     '-o', color=colors[method], label=labels[method], linewidth=2, markersize=6)

    ax1.set_xlabel('SNR (dB)')
    ax1.set_ylabel('MAE (ppm)')
    ax1.set_title('Noise Robustness - MAE')
    ax1.legend()
    ax1.invert_xaxis()

    ax2.set_xlabel('SNR (dB)')
    ax2.set_ylabel('R²')
    ax2.set_title('Noise Robustness - R²')
    ax2.legend()
    ax2.invert_xaxis()

    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, 'noise_robustness.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'noise_robustness.pdf'), bbox_inches='tight')
    plt.close('all')
    print("抗噪声能力图已保存")


# ============================================================
# 7. 主程序
# ============================================================

def main():
    print("=" * 60)
    print("方法对比实验: 1D-CNN vs DA vs PLS")
    print("=" * 60)

    # 加载数据
    X, y = load_dataset()
    if X is None:
        return

    # 划分训练/测试集
    from sklearn.model_selection import train_test_split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print(f"\n数据集: 训练 {len(X_train)} 样本, 测试 {len(X_test)} 样本")
    print(f"浓度范围: {y.min():.0f} - {y.max():.0f} ppm")

    # 评估三种方法
    results = evaluate_methods(X_train, y_train, X_test, y_test)

    # 低浓度评估
    print("\n--- 低浓度区间 (<100 ppm) ---")
    low_results = evaluate_low_concentration(X_test, y_test, results, threshold=100)
    for method, res in low_results.items():
        print(f"  {method}: MAE={res['mae']:.2f}, R2={res['r2']:.4f}")

    # 抗噪声评估
    print("\n--- 抗噪声能力测试 ---")
    noise_results = evaluate_noise_robustness(X_train, y_train, X_test, y_test)
    for method in ['DA', 'PLS', 'CNN']:
        if noise_results[method]['mae']:
            print(f"  {method}: SNR=20dB 时 MAE={noise_results[method]['mae'][-1]:.2f}")

    # 保存结果
    output_dir = 'experiment_data'
    os.makedirs(output_dir, exist_ok=True)

    # 汇总表
    summary = []
    for method in ['DA', 'PLS', 'CNN']:
        row = {
            'Method': method,
            'MAE_ppm': results[method]['mae'],
            'RMSE_ppm': results[method]['rmse'],
            'R2': results[method]['r2'],
        }
        if method in low_results:
            row['MAE_low_ppm'] = low_results[method]['mae']
            row['R2_low'] = low_results[method]['r2']
        summary.append(row)

    df_summary = pd.DataFrame(summary)
    df_summary.to_csv(os.path.join(output_dir, 'method_comparison.csv'), index=False)
    print(f"\n对比结果已保存: {os.path.join(output_dir, 'method_comparison.csv')}")

    # 绘图
    plot_comparison(results, y_test, output_dir)
    plot_noise_robustness(noise_results, output_dir)

    print("\n对比实验完成！")


if __name__ == '__main__':
    main()
