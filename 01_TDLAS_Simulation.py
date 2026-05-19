"""
TDLAS 气体吸收光谱仿真代码
============================
基于 HITRAN 数据库参数，模拟阴燃环境特征气体（CO）的吸收特性。
模拟激光器的波长调制（WMS），生成包含本底噪声的二次谐波（2f）信号。
批量生成不同浓度带有高斯白噪声的2f信号，保存为CSV格式。

生成时间: Tue May 19 01:27:32 2026
"""

import numpy as np
import pandas as pd
import os
from dataclasses import dataclass


# ============================================================
# 1. 物理常数与 HITRAN 参数（CO 在 1550nm 附近的吸收线）
# ============================================================

@dataclass
class HITRANLine:
    """HITRAN 光谱线参数（对应 CO 分子 1550nm 附近吸收带）"""
    nu0: float          # 中心频率 (cm^-1) — 对应 ~1550nm (6451.6 cm^-1 附近)
    S: float            # 线强度 (cm^-1/(molecule·cm^-2)) @ 296K
    gamma_air: float    # 空气展宽半宽 (cm^-1/atm) @ 296K
    gamma_self: float   # 自展宽半宽 (cm^-1/atm) @ 296K
    n_air: float        # 空气展宽温度依赖指数
    E_pp: float         # 低态能量 (cm^-1)
    alpha: float        # 温度依赖系数


# CO 在 6300-6400 cm^-1 波段的典型吸收线参数（近似 HITRAN 数据）
CO_ABSORPTION_LINES = [
    HITRANLine(nu0=6380.288, S=1.28e-21, gamma_air=0.062, gamma_self=0.085, n_air=0.71, E_pp=1084.64, alpha=2.5),
    HITRANLine(nu0=6381.507, S=3.45e-22, gamma_air=0.060, gamma_self=0.083, n_air=0.70, E_pp=1120.33, alpha=2.3),
    HITRANLine(nu0=6383.125, S=8.72e-22, gamma_air=0.063, gamma_self=0.086, n_air=0.72, E_pp=1050.18, alpha=2.6),
    HITRANLine(nu0=6384.760, S=2.10e-22, gamma_air=0.061, gamma_self=0.084, n_air=0.71, E_pp=1185.50, alpha=2.4),
]

# 物理常数
C_LIGHT = 2.998e10      # 光速 (cm/s)
K_BOLTZMANN = 1.381e-23 # 玻尔兹曼常数 (J/K)
H_PLANCK = 6.626e-34    # 普朗克常数 (J·s)
AVOGADRO = 6.022e23     # 阿伏伽德罗常数
T_REF = 296.0           # 参考温度 (K)
P_ATM = 1.0             # 气压 (atm)


# ============================================================
# 2. 吸收截面与 Beer-Lambert 模型
# ============================================================

def voigt_profile(nu, nu0, gamma_L, gamma_G):
    """
    Voigt 线型函数（Lorentz 与 Gauss 卷积的近似）
    使用伪 Voigt 近似以提高计算效率。

    参数:
        nu:    频率数组 (cm^-1)
        nu0:   中心频率 (cm^-1)
        gamma_L: Lorentz 半宽 (cm^-1)
        gamma_G: Gauss 半宽 (cm^-1)
    返回:
        线型函数值（归一化）
    """
    # Lorentz 分量
    lorentz = gamma_L / (np.pi * ((nu - nu0)**2 + gamma_L**2))

    # Gauss 分量
    gauss = (np.sqrt(4 * np.log(2) / np.pi) / gamma_G) * np.exp(-4 * np.log(2) * ((nu - nu0) / gamma_G)**2)

    # 伪 Voigt 混合系数 (Thompson et al. 1987)
    f = gamma_L**2 / (gamma_L**2 + gamma_G**2)
    # 近似混合比例
    eta = 1.36603 * (f**0.5) - 0.47719 * f + 0.11116 * (f**1.5)

    return eta * lorentz + (1 - eta) * gauss


def compute_absorption_spectrum(nu_array, T, P, concentration, path_length, lines):
    """
    基于 Beer-Lambert 定律计算吸收光谱透射率。

    参数:
        nu_array:      频率网格 (cm^-1)
        T:             温度 (K)
        P:             气压 (atm)
        concentration: 气体浓度 (ppm)
        path_length:   光程长度 (cm)
        lines:         HITRAN 光谱线列表
    返回:
        tau: 光学厚度数组
        transmittance: 透射率数组
    """
    # 浓度换算: ppm -> molecule/cm^3 (理想气体)
    N_total = P * 101325 / (K_BOLTZMANN * T)  # 总分子数密度 (molecule/m^3)
    N_total_cm3 = N_total * 1e-6                # 转换为 molecule/cm^3
    N_absorber = N_total_cm3 * concentration * 1e-6  # 吸收分子数密度

    tau = np.zeros_like(nu_array)

    for line in lines:
        # 温度修正线强度
        S_T = line.S * (T_REF / T) ** line.n_air * np.exp(
            -line.E_pp * (1.0 / T - 1.0 / T_REF) * (H_PLANCK * C_LIGHT / K_BOLTZMANN)
        )

        # 压力展宽 Lorentz 半宽
        gamma_L = (P / P_ATM) * (
            line.gamma_air * (1 - concentration * 1e-6) + line.gamma_self * concentration * 1e-6
        ) * (T_REF / T) ** line.n_air

        # 多普勒展宽 Gauss 半宽
        gamma_G = line.nu0 * np.sqrt(2 * K_BOLTZMANN * T * AVOGADRO / (0.028 * 1e-3)) / C_LIGHT

        # 累加光学厚度
        profile = voigt_profile(nu_array, line.nu0, gamma_L, gamma_G)
        tau += N_absorber * S_T * profile * path_length

    transmittance = np.exp(-tau)
    return tau, transmittance


# ============================================================
# 3. WMS（波长调制光谱）仿真
# ============================================================

def simulate_wms_2f(nu_array, transmittance, modulation_index, modulation_freq, n_harmonics=2):
    """
    模拟波长调制光谱 (WMS) 的 2f 信号。

    原理: 激光器注入电流同时包含低频锯齿波扫描和高频正弦调制，
    导致瞬时频率为: nu(t) = nu_scan(t) + delta_nu * sin(2*pi*f_mod*t)
    2f 信号通过锁相放大器提取。

    参数:
        nu_array:         频率网格 (cm^-1)
        transmittance:    透射率数组
        modulation_index: 调制深度 (cm^-1)，即 delta_nu
        modulation_freq:  调制频率 (Hz)
        n_harmonics:      提取的谐波次数 (2 = 二次谐波)
    返回:
        signal_2f: 归一化的 2f 信号
    """
    n_points = len(nu_array)
    # 一个调制周期内的采样点数
    n_per_period = max(128, n_points // 10)

    # 对每个扫描点计算 WMS 信号
    signal_2f = np.zeros(n_points)

    for i in range(n_points):
        nu_center = nu_array[i]
        # 一个调制周期内的瞬时频率
        t_mod = np.linspace(0, 1.0 / modulation_freq, n_per_period, endpoint=False)
        theta = 2 * np.pi * modulation_freq * t_mod
        nu_instant = nu_center + modulation_index * np.sin(theta)

        # 插值得到瞬时透射率
        T_instant = np.interp(nu_instant, nu_array, transmittance, left=transmittance[0], right=transmittance[-1])

        # 通过傅里叶分解提取 n 次谐波分量
        # a_n = (2/N) * sum( T(t) * cos(n*theta) )
        cos_component = np.mean(T_instant * np.cos(n_harmonics * theta))
        sin_component = np.mean(T_instant * np.sin(n_harmonics * theta))

        # 2f 信号幅值
        signal_2f[i] = np.sqrt(cos_component**2 + sin_component**2)

    # 归一化: 减去直流基线，除以峰值
    signal_2f = signal_2f - np.mean(signal_2f)
    peak = np.max(np.abs(signal_2f))
    if peak > 0:
        signal_2f = signal_2f / peak

    return signal_2f


# ============================================================
# 4. 噪声模型
# ============================================================

def add_gaussian_noise(signal, snr_db):
    """
    给信号添加高斯白噪声。

    参数:
        signal: 输入信号
        snr_db: 信噪比 (dB)
    返回:
        含噪声的信号
    """
    signal_power = np.mean(signal**2)
    noise_power = signal_power / (10 ** (snr_db / 10))
    noise = np.random.normal(0, np.sqrt(noise_power), len(signal))
    return signal + noise


def add_baseline_drift(signal, drift_amplitude=0.02):
    """添加低频基线漂移（模拟实际系统中的温度漂移等）"""
    n = len(signal)
    drift = drift_amplitude * np.sin(2 * np.pi * np.linspace(0, 1, n) * 3)
    return signal + drift


# ============================================================
# 5. 批量数据生成
# ============================================================

def batch_generate_dataset(
    output_dir="tdlas_dataset",
    concentrations_ppm=None,
    n_samples_per_conc=100,
    snr_range_db=(20, 40),
    T=296.0,
    P=1.0,
    path_length_cm=50.0,
    modulation_index_cm=0.05,
    n_scan_points=512,
):
    """
    批量生成不同浓度的 TDLAS 2f 信号数据集。

    参数:
        output_dir:          输出目录
        concentrations_ppm:  浓度列表 (ppm)
        n_samples_per_conc:  每个浓度的样本数
        snr_range_db:        信噪比范围 (dB)
        T:                   温度 (K)
        P:                   气压 (atm)
        path_length_cm:      光程长度 (cm)
        modulation_index_cm: 调制深度 (cm^-1)
        n_scan_points:       扫描点数
    """
    if concentrations_ppm is None:
        # 阴燃环境 CO 浓度范围: 50 - 5000 ppm
        concentrations_ppm = [50, 100, 200, 500, 1000, 2000, 3000, 5000]

    os.makedirs(output_dir, exist_ok=True)

    # 频率网格 (覆盖 CO 吸收线区域)
    nu_center = 6382.0  # cm^-1
    nu_span = 6.0       # cm^-1
    nu_array = np.linspace(nu_center - nu_span / 2, nu_center + nu_span / 2, n_scan_points)

    all_data = []
    metadata_rows = []

    print("=" * 60)
    print("TDLAS 2f 信号数据集生成")
    print("=" * 60)

    for conc in concentrations_ppm:
        print(f"\n>>> 生成浓度 {conc} ppm ...")

        # 计算无噪声的参考 2f 信号
        tau, transmittance = compute_absorption_spectrum(
            nu_array, T, P, conc, path_length_cm, CO_ABSORPTION_LINES
        )
        signal_2f_ref = simulate_wms_2f(
            nu_array, transmittance,
            modulation_index=modulation_index_cm,
            modulation_freq=10e3
        )

        for sample_idx in range(n_samples_per_conc):
            # 随机信噪比
            snr = np.random.uniform(*snr_range_db)

            # 添加噪声
            signal_noisy = add_gaussian_noise(signal_2f_ref, snr)
            # 随机决定是否添加基线漂移 (50% 概率)
            if np.random.random() > 0.5:
                signal_noisy = add_baseline_drift(signal_noisy, drift_amplitude=np.random.uniform(0.01, 0.05))

            # 组装一行数据: [浓度, 信噪比, 信号点1, 信号点2, ..., 信号点N]
            row = [conc, snr] + signal_noisy.tolist()
            all_data.append(row)

            metadata_rows.append({
                "sample_id": f"CO_{conc}ppm_{sample_idx:04d}",
                "concentration_ppm": conc,
                "snr_db": round(snr, 2),
                "temperature_K": T,
                "pressure_atm": P,
                "path_length_cm": path_length_cm,
            })

    # 构建 DataFrame
    signal_cols = [f"signal_{i}" for i in range(n_scan_points)]
    columns = ["concentration_ppm", "snr_db"] + signal_cols
    df = pd.DataFrame(all_data, columns=columns)

    # 保存完整数据集
    csv_path = os.path.join(output_dir, "tdlas_2f_dataset.csv")
    df.to_csv(csv_path, index=False)
    print(f"\n完整数据集已保存: {csv_path}")
    print(f"  样本总数: {len(df)}")
    print(f"  信号维度: {n_scan_points}")
    print(f"  浓度范围: {min(concentrations_ppm)} - {max(concentrations_ppm)} ppm")

    # 保存元数据
    meta_path = os.path.join(output_dir, "metadata.csv")
    pd.DataFrame(metadata_rows).to_csv(meta_path, index=False)
    print(f"  元数据已保存: {meta_path}")

    # 按浓度分别保存（便于单独分析）
    for conc in concentrations_ppm:
        subset = df[df["concentration_ppm"] == conc]
        conc_path = os.path.join(output_dir, f"co_{conc}ppm.csv")
        subset.to_csv(conc_path, index=False)

    # 保存参考信号（无噪声）
    ref_data = []
    for conc in concentrations_ppm:
        _, transmittance = compute_absorption_spectrum(
            nu_array, T, P, conc, path_length_cm, CO_ABSORPTION_LINES
        )
        ref_2f = simulate_wms_2f(nu_array, transmittance, modulation_index_cm, 10e3)
        ref_data.append([conc] + ref_2f.tolist())

    ref_columns = ["concentration_ppm"] + signal_cols
    ref_df = pd.DataFrame(ref_data, columns=ref_columns)
    ref_path = os.path.join(output_dir, "reference_2f_signals.csv")
    ref_df.to_csv(ref_path, index=False)
    print(f"  参考信号已保存: {ref_path}")

    print("\n" + "=" * 60)
    print("数据集生成完成！")
    print("=" * 60)

    return df


# ============================================================
# 6. 主程序入口
# ============================================================

if __name__ == "__main__":
    # 设置随机种子以保证可复现
    np.random.seed(42)

    # 生成数据集
    df = batch_generate_dataset(
        output_dir="tdlas_dataset",
        concentrations_ppm=[50, 100, 200, 500, 1000, 2000, 3000, 5000],
        n_samples_per_conc=100,
        snr_range_db=(20, 40),
        T=296.0,
        P=1.0,
        path_length_cm=50.0,
        modulation_index_cm=0.05,
        n_scan_points=512,
    )

    # 打印数据集摘要
    print("\n数据集统计摘要:")
    print(df.groupby("concentration_ppm")["snr_db"].describe())
