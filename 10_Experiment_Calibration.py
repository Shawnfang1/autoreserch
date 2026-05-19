"""
实验1：CO 浓度梯度数据采集与校准曲线
=====================================
自动化采集 CO 标气的 2f 信号，生成真实校准曲线。
支持串口实时采集和 Demo 模式（无硬件测试）。

用法:
    python 10_Experiment_Calibration.py --port COM3          # 串口采集
    python 10_Experiment_Calibration.py --demo               # Demo 模式
    python 10_Experiment_Calibration.py --analyze data.csv   # 分析已有数据

生成时间: 2026-05-19
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats
from scipy.signal import savgol_filter
import serial
import serial.tools.list_ports
import struct
import time
import os
import sys
import json
import argparse
from datetime import datetime


# ============================================================
# 1. 串口通信
# ============================================================

def list_ports():
    """列出可用串口"""
    ports = serial.tools.list_ports.comports()
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device} - {p.description}")
    return ports


def auto_select_port():
    """自动选择串口"""
    ports = serial.tools.list_ports.comports()
    keywords = ["USB", "CH340", "CP2102", "FTDI", "STLink", "Virtual COM"]
    for p in ports:
        for kw in keywords:
            if kw.lower() in p.description.lower():
                return p.device
    return ports[0].device if ports else None


def read_2f_frame(ser, n_points=512):
    """
    从串口读取一帧 2f 信号数据。

    协议: 0xAA 0x55 [2*N bytes float32] 0x0D 0x0A
    如果读取失败返回 None
    """
    # 寻找帧头
    while True:
        b = ser.read(1)
        if len(b) == 0:
            return None
        if b == b'\xAA':
            b2 = ser.read(1)
            if b2 == b'\x55':
                break

    # 读取数据
    data_bytes = ser.read(n_points * 4)
    if len(data_bytes) < n_points * 4:
        return None

    # 读取帧尾
    tail = ser.read(2)
    if tail != b'\x0D\x0A':
        return None

    # 解析 float32
    values = struct.unpack(f'<{n_points}f', data_bytes)
    return np.array(values)


def read_ascii_frame(ser, n_points=512):
    """
    从串口读取 ASCII 格式的 2f 数据（逗号分隔）。
    """
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    if not line:
        return None
    try:
        values = [float(x) for x in line.split(',')]
        if len(values) == n_points:
            return np.array(values)
    except ValueError:
        pass
    return None


# ============================================================
# 2. 数据采集
# ============================================================

class CalibrationExperiment:
    """CO 浓度梯度校准实验"""

    def __init__(self, port=None, baudrate=115200, n_points=512,
                 n_repeats=10, settle_time=30, demo=False):
        """
        参数:
            port: 串口号，None 则自动选择
            baudrate: 波特率
            n_points: 每帧采样点数
            n_repeats: 每个浓度重复次数
            settle_time: 浓度切换后等待稳定时间（秒）
            demo: 是否使用 Demo 模式
        """
        self.baudrate = baudrate
        self.n_points = n_points
        self.n_repeats = n_repeats
        self.settle_time = settle_time
        self.demo = demo
        self.ser = None

        if not demo:
            if port is None:
                port = auto_select_port()
            if port is None:
                print("错误: 未找到串口设备。使用 --demo 进入演示模式。")
                sys.exit(1)
            print(f"连接串口: {port} @ {baudrate} bps")
            self.ser = serial.Serial(port, baudrate, timeout=2.0)
            time.sleep(2)  # 等待连接稳定

    def generate_demo_signal(self, concentration):
        """Demo 模式：生成模拟 2f 信号"""
        t = np.linspace(0, 2 * np.pi, self.n_points)
        # 2f 信号近似：M 形双峰，幅值与浓度正相关
        amplitude = concentration * 0.001  # ppm -> 幅值缩放
        noise = np.random.normal(0, amplitude * 0.02, self.n_points)
        signal = amplitude * (np.cos(2 * t) - 0.3 * np.cos(4 * t)) + noise
        return signal

    def read_one_frame(self):
        """读取一帧数据"""
        if self.demo:
            return None  # 由外部设置浓度
        return read_2f_frame(self.ser, self.n_points)

    def collect_at_concentration(self, concentration, n_samples=None):
        """
        在指定浓度下采集数据。

        返回: dict with 'signals', 'amplitudes', 'timestamps'
        """
        if n_samples is None:
            n_samples = self.n_repeats

        signals = []
        amplitudes = []
        timestamps = []

        print(f"\n  浓度 {concentration} ppm，等待 {self.settle_time}s 稳定...")
        if not self.demo:
            time.sleep(self.settle_time)

        for i in range(n_samples):
            if self.demo:
                sig = self.generate_demo_signal(concentration)
                time.sleep(0.1)  # 模拟采样延迟
            else:
                sig = self.read_one_frame()
                if sig is None:
                    print(f"    第 {i+1} 帧读取失败，跳过")
                    continue

            # 计算 2f 幅值（峰值 - 谷值）
            amp = np.max(sig) - np.min(sig)
            signals.append(sig)
            amplitudes.append(amp)
            timestamps.append(time.time())

            if (i + 1) % 5 == 0:
                print(f"    已采集 {i+1}/{n_samples} 帧，幅值={amp:.4f}")

        return {
            'concentration': concentration,
            'signals': np.array(signals),
            'amplitudes': np.array(amplitudes),
            'timestamps': np.array(timestamps),
        }

    def run_calibration(self, concentrations=None):
        """
        运行完整的校准实验。

        参数:
            concentrations: CO 浓度列表（ppm），默认 [0,20,50,100,200,500,1000,2000]
        """
        if concentrations is None:
            concentrations = [0, 20, 50, 100, 200, 500, 1000, 2000]

        print("=" * 60)
        print("CO 浓度梯度校准实验")
        print(f"浓度点: {concentrations} ppm")
        print(f"每点重复: {self.n_repeats} 次")
        print(f"模式: {'Demo' if self.demo else '串口采集'}")
        print("=" * 60)

        all_data = []
        for conc in concentrations:
            result = self.collect_at_concentration(conc)
            all_data.append(result)

        # 汇总
        summary = []
        for r in all_data:
            summary.append({
                'concentration_ppm': r['concentration'],
                'mean_amplitude': np.mean(r['amplitudes']),
                'std_amplitude': np.std(r['amplitudes']),
                'n_samples': len(r['amplitudes']),
                'cv_percent': np.std(r['amplitudes']) / np.mean(r['amplitudes']) * 100
                               if np.mean(r['amplitudes']) > 0 else 0,
            })

        return all_data, pd.DataFrame(summary)

    def close(self):
        """关闭串口"""
        if self.ser:
            self.ser.close()


# ============================================================
# 3. 数据保存
# ============================================================

def save_raw_data(all_data, output_dir='experiment_data'):
    """保存原始数据为 CSV"""
    os.makedirs(output_dir, exist_ok=True)

    # 保存每帧幅值
    rows = []
    for r in all_data:
        for i, (amp, ts) in enumerate(zip(r['amplitudes'], r['timestamps'])):
            rows.append({
                'concentration_ppm': r['concentration'],
                'repeat_index': i,
                'amplitude': amp,
                'timestamp': ts,
            })
    df = pd.DataFrame(rows)
    csv_path = os.path.join(output_dir, 'calibration_raw.csv')
    df.to_csv(csv_path, index=False)
    print(f"原始数据已保存: {csv_path}")

    # 保存 2f 信号波形
    for r in all_data:
        conc = r['concentration']
        sig_path = os.path.join(output_dir, f'signal_{conc}ppm.npy')
        np.save(sig_path, r['signals'])

    return df


def save_summary(summary_df, output_dir='experiment_data'):
    """保存汇总表"""
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, 'calibration_summary.csv')
    summary_df.to_csv(csv_path, index=False)
    print(f"汇总表已保存: {csv_path}")


# ============================================================
# 4. 校准分析
# ============================================================

def analyze_calibration(summary_df, raw_df=None):
    """
    分析校准数据，计算 LOD、线性度、R²。

    返回: dict with calibration parameters
    """
    x = summary_df['concentration_ppm'].values
    y = summary_df['mean_amplitude'].values
    y_std = summary_df['std_amplitude'].values

    # 线性拟合
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
    r_squared = r_value ** 2

    # LOD = 3 * σ_blank / slope
    # 使用最低浓度（通常 0 ppm）的标准差
    sigma_blank = summary_df.loc[
        summary_df['concentration_ppm'] == summary_df['concentration_ppm'].min(),
        'std_amplitude'
    ].values[0]
    lod = 3 * sigma_blank / slope if slope > 0 else float('inf')

    # LOQ = 10 * σ_blank / slope
    loq = 10 * sigma_blank / slope if slope > 0 else float('inf')

    # 非线性度（最大偏差 / 满量程）
    y_pred = slope * x + intercept
    max_dev = np.max(np.abs(y - y_pred))
    full_scale = y[-1] - y[0]
    nonlinearity = max_dev / full_scale * 100 if full_scale > 0 else 0

    results = {
        'slope': slope,
        'intercept': intercept,
        'r_squared': r_squared,
        'p_value': p_value,
        'std_err': std_err,
        'lod_ppm': lod,
        'loq_ppm': loq,
        'nonlinearity_percent': nonlinearity,
        'sigma_blank': sigma_blank,
    }

    print("\n" + "=" * 60)
    print("校准分析结果")
    print("=" * 60)
    print(f"  斜率:        {slope:.6f}")
    print(f"  截距:        {intercept:.6f}")
    print(f"  R2:          {r_squared:.6f}")
    print(f"  LOD:         {lod:.1f} ppm")
    print(f"  LOQ:         {loq:.1f} ppm")
    print(f"  非线性度:    {nonlinearity:.2f}%")
    print("=" * 60)

    return results


# ============================================================
# 5. 可视化
# ============================================================

def plot_calibration(summary_df, cal_results, output_dir='experiment_data'):
    """绘制校准曲线"""
    os.makedirs(output_dir, exist_ok=True)

    # Nature 风格设置
    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.sans-serif': ['Arial', 'DejaVu Sans'],
        'font.size': 10,
        'axes.linewidth': 0.8,
        'axes.spines.right': False,
        'axes.spines.top': False,
        'figure.dpi': 150,
    })

    x = summary_df['concentration_ppm'].values
    y = summary_df['mean_amplitude'].values
    y_std = summary_df['std_amplitude'].values

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # 左图：校准曲线
    ax1.errorbar(x, y, yerr=y_std, fmt='o', color='#2E86AB', markersize=8,
                 capsize=4, capthick=1.5, elinewidth=1.5, label='Measured')
    slope = cal_results['slope']
    intercept = cal_results['intercept']
    x_fit = np.linspace(0, max(x) * 1.1, 100)
    ax1.plot(x_fit, slope * x_fit + intercept, '--', color='#E84855',
             linewidth=1.5, label=f'Linear fit (R²={cal_results["r_squared"]:.4f})')
    ax1.set_xlabel('CO Concentration (ppm)')
    ax1.set_ylabel('2f Signal Amplitude')
    ax1.set_title('(a) Calibration Curve')
    ax1.legend(loc='upper left')
    ax1.set_xlim(-50, max(x) * 1.1)
    ax1.set_ylim(bottom=-0.05 * max(y))

    # 标注 LOD
    lod = cal_results['lod_ppm']
    ax1.axvline(x=lod, color='#F6AE2D', linestyle=':', linewidth=1.5)
    ax1.annotate(f'LOD = {lod:.1f} ppm', xy=(lod, slope * lod + intercept),
                 xytext=(lod + 100, slope * lod + intercept + 0.05),
                 arrowprops=dict(arrowstyle='->', color='#F6AE2D'),
                 fontsize=9, color='#F6AE2D')

    # 右图：残差
    y_pred = slope * x + intercept
    residuals = y - y_pred
    ax2.bar(x, residuals, width=max(x) * 0.08, color='#A23B72', alpha=0.7)
    ax2.axhline(y=0, color='gray', linestyle='-', linewidth=0.5)
    ax2.set_xlabel('CO Concentration (ppm)')
    ax2.set_ylabel('Residual')
    ax2.set_title('(b) Residuals')

    plt.tight_layout()

    # 保存
    fig.savefig(os.path.join(output_dir, 'calibration_curve.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'calibration_curve.pdf'), bbox_inches='tight')
    print(f"校准曲线图已保存")

    # 低浓度放大图
    fig2, ax3 = plt.subplots(figsize=(6, 5))
    low_mask = x <= 200
    ax3.errorbar(x[low_mask], y[low_mask], yerr=y_std[low_mask],
                 fmt='s', color='#2E86AB', markersize=8, capsize=4,
                 capthick=1.5, elinewidth=1.5, label='Measured')
    x_low = np.linspace(0, 250, 100)
    ax3.plot(x_low, slope * x_low + intercept, '--', color='#E84855',
             linewidth=1.5, label='Linear fit')
    ax3.set_xlabel('CO Concentration (ppm)')
    ax3.set_ylabel('2f Signal Amplitude')
    ax3.set_title('Low Concentration Range (0-200 ppm)')
    ax3.legend()
    ax3.set_xlim(-10, 250)

    fig2.savefig(os.path.join(output_dir, 'calibration_low_conc.png'), dpi=600, bbox_inches='tight')
    fig2.savefig(os.path.join(output_dir, 'calibration_low_conc.pdf'), bbox_inches='tight')
    print(f"低浓度放大图已保存")

    plt.close('all')


def plot_signal_examples(all_data, output_dir='experiment_data'):
    """绘制典型 2f 信号波形"""
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

    # 选取 3 个代表性浓度
    selected = [r for r in all_data if r['concentration'] in [50, 500, 2000]]
    if len(selected) < 3:
        selected = all_data[:3]

    colors = ['#2E86AB', '#E84855', '#F6AE2D']
    fig, ax = plt.subplots(figsize=(8, 5))

    for i, r in enumerate(selected):
        sig = r['signals'][0]  # 取第一帧
        t = np.linspace(0, 1, len(sig))
        ax.plot(t, sig, color=colors[i], linewidth=1.2,
                label=f'{r["concentration"]} ppm')

    ax.set_xlabel('Normalized Phase')
    ax.set_ylabel('2f Signal')
    ax.set_title('WMS-2f Signals at Different CO Concentrations')
    ax.legend()

    fig.savefig(os.path.join(output_dir, 'signal_examples.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'signal_examples.pdf'), bbox_inches='tight')
    print(f"信号波形图已保存")
    plt.close('all')


# ============================================================
# 6. 主程序
# ============================================================

def load_and_analyze(csv_path):
    """加载已有数据并分析"""
    df = pd.read_csv(csv_path)

    # 汇总
    summary = df.groupby('concentration_ppm').agg(
        mean_amplitude=('amplitude', 'mean'),
        std_amplitude=('amplitude', 'std'),
        n_samples=('amplitude', 'count'),
    ).reset_index()
    summary['cv_percent'] = summary['std_amplitude'] / summary['mean_amplitude'] * 100

    cal_results = analyze_calibration(summary)
    plot_calibration(summary, cal_results)
    return summary, cal_results


def main():
    parser = argparse.ArgumentParser(description='CO 浓度梯度校准实验')
    parser.add_argument('--port', type=str, default=None, help='串口号 (如 COM3)')
    parser.add_argument('--baudrate', type=int, default=115200, help='波特率')
    parser.add_argument('--demo', action='store_true', help='Demo 模式（无硬件）')
    parser.add_argument('--repeats', type=int, default=10, help='每浓度重复次数')
    parser.add_argument('--settle', type=int, default=30, help='浓度切换等待时间(秒)')
    parser.add_argument('--analyze', type=str, default=None, help='分析已有 CSV 文件')
    parser.add_argument('--output', type=str, default='experiment_data', help='输出目录')
    args = parser.parse_args()

    # 分析已有数据
    if args.analyze:
        load_and_analyze(args.analyze)
        return

    # 运行实验
    exp = CalibrationExperiment(
        port=args.port,
        baudrate=args.baudrate,
        n_repeats=args.repeats,
        settle_time=args.settle,
        demo=args.demo,
    )

    try:
        all_data, summary_df = exp.run_calibration()
    finally:
        exp.close()

    # 保存
    raw_df = save_raw_data(all_data, args.output)
    save_summary(summary_df, args.output)

    # 分析
    cal_results = analyze_calibration(summary_df)

    # 绘图
    plot_calibration(summary_df, cal_results, args.output)
    plot_signal_examples(all_data, args.output)

    # 保存校准参数
    with open(os.path.join(args.output, 'calibration_params.json'), 'w') as f:
        json.dump(cal_results, f, indent=2, default=str)
    print(f"\n校准参数已保存: {os.path.join(args.output, 'calibration_params.json')}")

    print("\n实验完成！")


if __name__ == '__main__':
    main()
