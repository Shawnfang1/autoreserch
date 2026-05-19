"""
实验4：重复性与 24h 稳定性测试
===============================
短期重复性（20 次连续测量）和 24h 长期稳定性自动采集。

用法:
    python 13_Experiment_Stability.py --port COM3         # 串口采集
    python 13_Experiment_Stability.py --demo              # Demo 模式
    python 13_Experiment_Stability.py --analyze data.csv  # 分析已有数据

生成时间: 2026-05-19
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter, HourLocator
from scipy import stats
import serial
import serial.tools.list_ports
import time
import os
import sys
import json
import argparse
from datetime import datetime, timedelta


# ============================================================
# 1. 串口通信（复用）
# ============================================================

def auto_select_port():
    """自动选择串口"""
    import serial.tools.list_ports
    ports = serial.tools.list_ports.comports()
    keywords = ["USB", "CH340", "CP2102", "FTDI", "STLink", "Virtual COM"]
    for p in ports:
        for kw in keywords:
            if kw.lower() in p.description.lower():
                return p.device
    return ports[0].device if ports else None


def read_concentration(ser):
    """从串口读取浓度预测值"""
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    if line.startswith('PREDICT:'):
        parts = line[8:].split(',')
        if len(parts) >= 1:
            return float(parts[0])
    elif line.startswith('CONC:'):
        return float(line[5:])
    return None


# ============================================================
# 2. 重复性测试
# ============================================================

def run_repeatability_test(ser=None, concentration=500, n_repeats=20,
                           interval=5, demo=False):
    """
    短期重复性测试。

    参数:
        ser: 串口对象
        concentration: 标气浓度 (ppm)
        n_repeats: 重复次数
        interval: 采样间隔 (秒)
        demo: Demo 模式

    返回: dict with repeatability results
    """
    print(f"\n{'='*60}")
    print(f"短期重复性测试: {concentration} ppm, {n_repeats} 次")
    print(f"{'='*60}")

    values = []
    timestamps = []

    for i in range(n_repeats):
        if demo:
            # 模拟测量值（加入随机波动）
            value = concentration + np.random.normal(0, concentration * 0.015)
            time.sleep(0.5)
        else:
            # 从串口读取
            value = None
            for _ in range(10):  # 最多读 10 次
                value = read_concentration(ser)
                if value is not None:
                    break
                time.sleep(0.5)

        if value is not None:
            values.append(value)
            timestamps.append(datetime.now())
            print(f"  [{i+1:2d}/{n_repeats}] {value:.1f} ppm")

        if not demo and i < n_repeats - 1:
            time.sleep(interval)

    values = np.array(values)
    mean_val = np.mean(values)
    std_val = np.std(values)
    rsd = std_val / mean_val * 100 if mean_val > 0 else 0

    results = {
        'concentration_ppm': concentration,
        'n_measurements': len(values),
        'mean_ppm': float(mean_val),
        'std_ppm': float(std_val),
        'rsd_percent': float(rsd),
        'min_ppm': float(np.min(values)),
        'max_ppm': float(np.max(values)),
        'values': values.tolist(),
        'timestamps': [t.isoformat() for t in timestamps],
    }

    print(f"\n  平均值: {mean_val:.1f} ppm")
    print(f"  标准差: {std_val:.2f} ppm")
    print(f"  RSD:    {rsd:.2f}%")
    print(f"  范围:   [{np.min(values):.1f}, {np.max(values):.1f}] ppm")

    return results


# ============================================================
# 3. 24h 稳定性测试
# ============================================================

def run_stability_test(ser=None, concentration=200, duration_hours=24,
                       interval_minutes=10, demo=False):
    """
    24h 长期稳定性测试。

    参数:
        ser: 串口对象
        concentration: 标气浓度 (ppm)
        duration_hours: 测试时长 (小时)
        interval_minutes: 采样间隔 (分钟)
        demo: Demo 模式

    返回: dict with stability results
    """
    print(f"\n{'='*60}")
    print(f"24h 稳定性测试: {concentration} ppm, {duration_hours}h")
    print(f"采样间隔: {interval_minutes} 分钟")
    print(f"{'='*60}")

    total_samples = int(duration_hours * 60 / interval_minutes)
    values = []
    timestamps = []
    start_time = datetime.now()

    for i in range(total_samples):
        target_time = start_time + timedelta(minutes=i * interval_minutes)
        now = datetime.now()
        if target_time > now and not demo:
            wait_seconds = (target_time - now).total_seconds()
            if wait_seconds > 0:
                time.sleep(wait_seconds)

        if demo:
            # 模拟 24h 漂移
            hours_elapsed = i * interval_minutes / 60
            # 缓慢漂移 + 日周期波动 + 随机噪声
            drift = 0.02 * hours_elapsed  # 0.02 ppm/h 漂移
            daily_cycle = 0.5 * np.sin(2 * np.pi * hours_elapsed / 24)  # 日周期
            noise = np.random.normal(0, concentration * 0.008)
            value = concentration + drift + daily_cycle + noise
            time.sleep(0.1)
        else:
            value = None
            for _ in range(10):
                value = read_concentration(ser)
                if value is not None:
                    break
                time.sleep(0.5)

        if value is not None:
            values.append(value)
            timestamps.append(datetime.now())

            if (i + 1) % 6 == 0:  # 每小时报告一次
                elapsed = (datetime.now() - start_time).total_seconds() / 3600
                print(f"  [{elapsed:.1f}h] 已采集 {i+1}/{total_samples} 样本, "
                      f"当前值={value:.1f} ppm")

    values = np.array(values)
    timestamps = np.array(timestamps)

    # 计算统计量
    mean_val = np.mean(values)
    std_val = np.std(values)
    rsd = std_val / mean_val * 100 if mean_val > 0 else 0

    # 线性漂移分析
    hours_elapsed = np.array([(t - timestamps[0]).total_seconds() / 3600
                              for t in timestamps])
    slope, intercept, r_value, _, _ = stats.linregress(hours_elapsed, values)
    drift_per_hour = slope

    results = {
        'concentration_ppm': concentration,
        'duration_hours': duration_hours,
        'interval_minutes': interval_minutes,
        'n_samples': len(values),
        'mean_ppm': float(mean_val),
        'std_ppm': float(std_val),
        'rsd_percent': float(rsd),
        'drift_ppm_per_hour': float(drift_per_hour),
        'drift_r_squared': float(r_value ** 2),
        'values': values.tolist(),
        'timestamps': [t.isoformat() for t in timestamps],
    }

    print(f"\n  平均值:     {mean_val:.1f} ppm")
    print(f"  标准差:     {std_val:.2f} ppm")
    print(f"  RSD:        {rsd:.2f}%")
    print(f"  漂移率:     {drift_per_hour:.3f} ppm/h")
    print(f"  漂移 R2:    {r_value**2:.4f}")

    return results


# ============================================================
# 4. 可视化
# ============================================================

def plot_repeatability(results, output_dir='experiment_data'):
    """绘制重复性测试图"""
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

    values = np.array(results['values'])
    mean_val = results['mean_ppm']
    std_val = results['std_ppm']
    rsd = results['rsd_percent']

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # 时序图
    ax1.plot(range(1, len(values) + 1), values, 'o-', color='#2E86AB',
             markersize=6, linewidth=1.2)
    ax1.axhline(mean_val, color='#E84855', linestyle='--', linewidth=1.5,
                label=f'Mean = {mean_val:.1f} ppm')
    ax1.axhline(mean_val + 2 * std_val, color='#F6AE2D', linestyle=':',
                linewidth=1, label=f'±2σ ({2*std_val:.2f} ppm)')
    ax1.axhline(mean_val - 2 * std_val, color='#F6AE2D', linestyle=':', linewidth=1)
    ax1.set_xlabel('Measurement Index')
    ax1.set_ylabel('Concentration (ppm)')
    ax1.set_title(f'(a) Repeatability Test ({results["concentration_ppm"]} ppm)\n'
                  f'RSD = {rsd:.2f}%')
    ax1.legend(loc='upper right')

    # 直方图
    ax2.hist(values, bins=10, color='#2E86AB', alpha=0.7, edgecolor='white')
    ax2.axvline(mean_val, color='#E84855', linestyle='--', linewidth=1.5,
                label=f'Mean = {mean_val:.1f}')
    ax2.set_xlabel('Concentration (ppm)')
    ax2.set_ylabel('Count')
    ax2.set_title('(b) Distribution')
    ax2.legend()

    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, 'repeatability.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'repeatability.pdf'), bbox_inches='tight')
    plt.close('all')
    print("重复性测试图已保存")


def plot_stability(results, output_dir='experiment_data'):
    """绘制 24h 稳定性测试图"""
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

    values = np.array(results['values'])
    timestamps = [datetime.fromisoformat(t) for t in results['timestamps']]
    hours_elapsed = np.array([(t - timestamps[0]).total_seconds() / 3600
                              for t in timestamps])

    mean_val = results['mean_ppm']
    std_val = results['std_ppm']
    rsd = results['rsd_percent']
    drift = results['drift_ppm_per_hour']

    # 漂移拟合线
    slope = drift
    intercept = mean_val - slope * np.mean(hours_elapsed)
    fit_line = slope * hours_elapsed + intercept

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    # 上图：时间序列
    ax1.plot(hours_elapsed, values, '-', color='#2E86AB', linewidth=1.2,
             label='Measured')
    ax1.plot(hours_elapsed, fit_line, '--', color='#E84855', linewidth=1.5,
             label=f'Linear fit ({drift:.3f} ppm/h)')
    ax1.axhline(mean_val, color='gray', linestyle=':', linewidth=0.8)
    ax1.fill_between(hours_elapsed, mean_val - 2 * std_val, mean_val + 2 * std_val,
                     alpha=0.1, color='#F6AE2D', label=f'±2σ ({2*std_val:.2f} ppm)')
    ax1.set_xlabel('Time (hours)')
    ax1.set_ylabel('Concentration (ppm)')
    ax1.set_title(f'24h Stability Test ({results["concentration_ppm"]} ppm)\n'
                  f'RSD = {rsd:.2f}%, Drift = {drift:.3f} ppm/h')
    ax1.legend(loc='upper left')
    ax1.set_xlim(0, max(hours_elapsed))

    # 下图：每小时箱线图
    df = pd.DataFrame({'hour': np.floor(hours_elapsed).astype(int), 'value': values})
    hourly_groups = [group['value'].values for _, group in df.groupby('hour')]
    hourly_labels = [f'{h}h' for h in range(len(hourly_groups))]

    bp = ax2.boxplot(hourly_groups, labels=hourly_labels, patch_artist=True)
    for patch in bp['boxes']:
        patch.set_facecolor('#2E86AB')
        patch.set_alpha(0.6)
    ax2.axhline(mean_val, color='#E84855', linestyle='--', linewidth=1)
    ax2.set_xlabel('Time (hour)')
    ax2.set_ylabel('Concentration (ppm)')
    ax2.set_title('Hourly Distribution')
    ax2.tick_params(axis='x', rotation=45)

    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, 'stability_24h.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'stability_24h.pdf'), bbox_inches='tight')
    plt.close('all')
    print("24h 稳定性测试图已保存")


# ============================================================
# 5. 数据保存
# ============================================================

def save_results(repeatability, stability, output_dir='experiment_data'):
    """保存测试结果"""
    os.makedirs(output_dir, exist_ok=True)

    # 保存重复性数据
    if repeatability:
        df_rep = pd.DataFrame({
            'measurement_index': range(1, len(repeatability['values']) + 1),
            'concentration_ppm': repeatability['values'],
            'timestamp': repeatability['timestamps'],
        })
        df_rep.to_csv(os.path.join(output_dir, 'repeatability_raw.csv'), index=False)

    # 保存稳定性数据
    if stability:
        df_stab = pd.DataFrame({
            'sample_index': range(1, len(stability['values']) + 1),
            'concentration_ppm': stability['values'],
            'timestamp': stability['timestamps'],
        })
        df_stab.to_csv(os.path.join(output_dir, 'stability_24h_raw.csv'), index=False)

    # 汇总 JSON
    summary = {
        'repeatability': {k: v for k, v in (repeatability or {}).items()
                          if k not in ['values', 'timestamps']},
        'stability': {k: v for k, v in (stability or {}).items()
                      if k not in ['values', 'timestamps']},
    }
    with open(os.path.join(output_dir, 'stability_summary.json'), 'w') as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"结果已保存到: {output_dir}/")


# ============================================================
# 6. 分析已有数据
# ============================================================

def analyze_repeatability_csv(csv_path):
    """分析已有的重复性数据"""
    df = pd.read_csv(csv_path)
    values = df['concentration_ppm'].values

    results = {
        'concentration_ppm': df.get('concentration_ppm', pd.Series([0])).iloc[0],
        'n_measurements': len(values),
        'mean_ppm': float(np.mean(values)),
        'std_ppm': float(np.std(values)),
        'rsd_percent': float(np.std(values) / np.mean(values) * 100),
        'min_ppm': float(np.min(values)),
        'max_ppm': float(np.max(values)),
        'values': values.tolist(),
    }
    return results


def analyze_stability_csv(csv_path):
    """分析已有的稳定性数据"""
    df = pd.read_csv(csv_path)
    values = df['concentration_ppm'].values

    if 'timestamp' in df.columns:
        timestamps = [datetime.fromisoformat(t) for t in df['timestamp']]
    else:
        timestamps = [datetime.now() + timedelta(minutes=10*i) for i in range(len(values))]

    hours_elapsed = np.array([(t - timestamps[0]).total_seconds() / 3600
                              for t in timestamps])
    slope, intercept, r_value, _, _ = stats.linregress(hours_elapsed, values)

    results = {
        'concentration_ppm': 200,  # 默认
        'duration_hours': float(hours_elapsed[-1]),
        'n_samples': len(values),
        'mean_ppm': float(np.mean(values)),
        'std_ppm': float(np.std(values)),
        'rsd_percent': float(np.std(values) / np.mean(values) * 100),
        'drift_ppm_per_hour': float(slope),
        'drift_r_squared': float(r_value ** 2),
        'values': values.tolist(),
        'timestamps': [t.isoformat() for t in timestamps],
    }
    return results


# ============================================================
# 7. 主程序
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='重复性与稳定性测试')
    parser.add_argument('--port', type=str, default=None, help='串口号')
    parser.add_argument('--baudrate', type=int, default=115200, help='波特率')
    parser.add_argument('--demo', action='store_true', help='Demo 模式')
    parser.add_argument('--repeats', type=int, default=20, help='重复性测试次数')
    parser.add_argument('--duration', type=int, default=24, help='稳定性测试时长(小时)')
    parser.add_argument('--interval', type=int, default=10, help='稳定性采样间隔(分钟)')
    parser.add_argument('--concentration', type=int, default=500, help='标气浓度(ppm)')
    parser.add_argument('--analyze-rep', type=str, default=None, help='分析已有重复性数据')
    parser.add_argument('--analyze-stab', type=str, default=None, help='分析已有稳定性数据')
    parser.add_argument('--output', type=str, default='experiment_data', help='输出目录')
    args = parser.parse_args()

    repeatability = None
    stability = None

    # 分析已有数据
    if args.analyze_rep:
        repeatability = analyze_repeatability_csv(args.analyze_rep)
        plot_repeatability(repeatability, args.output)
        save_results(repeatability, stability, args.output)
        return

    if args.analyze_stab:
        stability = analyze_stability_csv(args.analyze_stab)
        plot_stability(stability, args.output)
        save_results(repeatability, stability, args.output)
        return

    # 连接串口
    ser = None
    if not args.demo:
        port = args.port or auto_select_port()
        if port is None:
            print("未找到串口。使用 --demo 进入演示模式。")
            sys.exit(1)
        print(f"连接串口: {port}")
        ser = serial.Serial(port, args.baudrate, timeout=2.0)
        time.sleep(2)

    try:
        # 重复性测试
        repeatability = run_repeatability_test(
            ser=ser, concentration=args.concentration,
            n_repeats=args.repeats, demo=args.demo
        )
        plot_repeatability(repeatability, args.output)

        # 稳定性测试
        print(f"\n是否继续进行 {args.duration}h 稳定性测试？")
        print(f"（预计需要 {args.duration * 60 / args.interval:.0f} 个采样点）")

        stability = run_stability_test(
            ser=ser, concentration=args.concentration,
            duration_hours=args.duration,
            interval_minutes=args.interval,
            demo=args.demo
        )
        plot_stability(stability, args.output)

    finally:
        if ser:
            ser.close()

    # 保存结果
    save_results(repeatability, stability, args.output)

    print("\n稳定性测试完成！")


if __name__ == '__main__':
    main()
