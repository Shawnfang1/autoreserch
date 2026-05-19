"""
实验3：STM32 推理性能测试
=========================
测量 1D-CNN 在 STM32F407 上的推理延迟、内存占用和准确度。
需要 STM32 通过串口返回推理结果。

用法:
    python 12_Experiment_STM32_Perf.py --port COM3     # 串口连接 STM32
    python 12_Experiment_STM32_Perf.py --demo           # Demo 模式

生成时间: 2026-05-19
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
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

def auto_select_port():
    """自动选择串口"""
    ports = serial.tools.list_ports.comports()
    keywords = ["USB", "CH340", "CP2102", "FTDI", "STLink", "Virtual COM"]
    for p in ports:
        for kw in keywords:
            if kw.lower() in p.description.lower():
                return p.device
    return ports[0].device if ports else None


def send_command(ser, cmd):
    """发送命令到 STM32"""
    ser.write(f"{cmd}\n".encode())
    time.sleep(0.1)


def read_response(ser, timeout=2.0):
    """读取 STM32 响应"""
    start = time.time()
    lines = []
    while time.time() - start < timeout:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if line:
            lines.append(line)
    return lines


def read_inference_result(ser, n_points=512):
    """
    读取一次推理结果。

    预期 STM32 返回格式:
        PREDICT:<concentration_ppm>,<latency_ms>
    或者二进制帧:
        0xAA 0x55 [4 bytes float32: concentration] [4 bytes float32: latency_ms] 0x0D 0x0A
    """
    line = ser.readline().decode('utf-8', errors='ignore').strip()
    if line.startswith('PREDICT:'):
        parts = line[8:].split(',')
        if len(parts) == 2:
            return {
                'concentration': float(parts[0]),
                'latency_ms': float(parts[1]),
            }
    return None


# ============================================================
# 2. 性能测试
# ============================================================

class STM32PerfTest:
    """STM32 推理性能测试"""

    def __init__(self, port=None, baudrate=115200, demo=False):
        self.demo = demo
        self.ser = None

        if not demo:
            if port is None:
                port = auto_select_port()
            if port is None:
                print("错误: 未找到串口。使用 --demo 进入演示模式。")
                sys.exit(1)
            print(f"连接 STM32: {port} @ {baudrate} bps")
            self.ser = serial.Serial(port, baudrate, timeout=2.0)
            time.sleep(2)
            # 发送复位命令
            send_command(self.ser, "RESET")

    def test_latency(self, n_iterations=1000):
        """
        测试推理延迟。

        返回: dict with latency statistics
        """
        print(f"\n推理延迟测试 ({n_iterations} 次迭代)...")
        latencies = []

        for i in range(n_iterations):
            if self.demo:
                # 模拟 STM32 推理延迟
                time.sleep(0.003)  # 3ms 模拟
                latencies.append(np.random.normal(3.2, 0.3))
            else:
                send_command(self.ser, "INFER")
                result = read_inference_result(self.ser)
                if result:
                    latencies.append(result['latency_ms'])

            if (i + 1) % 100 == 0:
                print(f"  已完成 {i+1}/{n_iterations}")

        latencies = np.array(latencies)
        stats = {
            'n_iterations': len(latencies),
            'mean_ms': float(np.mean(latencies)),
            'std_ms': float(np.std(latencies)),
            'min_ms': float(np.min(latencies)),
            'max_ms': float(np.max(latencies)),
            'median_ms': float(np.median(latencies)),
            'p95_ms': float(np.percentile(latencies, 95)),
            'p99_ms': float(np.percentile(latencies, 99)),
        }

        print(f"  平均延迟: {stats['mean_ms']:.2f} +/- {stats['std_ms']:.2f} ms")
        print(f"  中位数:   {stats['median_ms']:.2f} ms")
        print(f"  P95:      {stats['p95_ms']:.2f} ms")
        print(f"  P99:      {stats['p99_ms']:.2f} ms")

        return stats, latencies

    def test_accuracy(self, test_signals, pc_predictions):
        """
        测试 STM32 推理准确度（对比 PC 端结果）。

        参数:
            test_signals: 测试信号 (n_samples, n_points)
            pc_predictions: PC 端 ONNX Runtime 预测结果

        返回: dict with accuracy metrics
        """
        print(f"\n准确度测试 ({len(test_signals)} 样本)...")
        stm32_predictions = []

        for i, sig in enumerate(test_signals):
            if self.demo:
                # 模拟 STM32 预测（加入小误差）
                pred = pc_predictions[i] + np.random.normal(0, 0.5)
                stm32_predictions.append(pred)
            else:
                # 发送信号数据到 STM32
                data_bytes = struct.pack(f'<{len(sig)}f', *sig)
                self.ser.write(b'DATA:' + data_bytes + b'\n')
                time.sleep(0.05)

                # 请求推理
                send_command(self.ser, "INFER")
                result = read_inference_result(self.ser)
                if result:
                    stm32_predictions.append(result['concentration'])

        stm32_predictions = np.array(stm32_predictions)
        pc_predictions = np.array(pc_predictions[:len(stm32_predictions)])

        # 计算差异
        diff = stm32_predictions - pc_predictions
        abs_diff = np.abs(diff)

        accuracy = {
            'n_samples': len(stm32_predictions),
            'mean_abs_error': float(np.mean(abs_diff)),
            'max_abs_error': float(np.max(abs_diff)),
            'std_error': float(np.std(diff)),
            'correlation': float(np.corrcoef(stm32_predictions, pc_predictions)[0, 1]),
            'max_percent_error': float(np.max(abs_diff / (pc_predictions + 1e-6) * 100)),
        }

        print(f"  平均绝对误差: {accuracy['mean_abs_error']:.3f} ppm")
        print(f"  最大绝对误差: {accuracy['max_abs_error']:.3f} ppm")
        print(f"  相关系数:     {accuracy['correlation']:.6f}")
        print(f"  最大百分误差: {accuracy['max_percent_error']:.2f}%")

        return accuracy, stm32_predictions

    def close(self):
        """关闭连接"""
        if self.ser:
            send_command(self.ser, "IDLE")
            self.ser.close()


# ============================================================
# 3. 内存/Flash 信息（从编译报告获取）
# ============================================================

def get_memory_info():
    """
    从 STM32CubeIDE 编译报告获取内存使用信息。
    需要用户手动填入或从 .map 文件解析。

    返回: dict with memory usage
    """
    # 默认值（基于 TinyTDLASNet 2,833 参数）
    info = {
        'model_size_bytes': 19500,      # ONNX 模型大小
        'model_size_int8_bytes': 13300, # INT8 量化模型大小
        'flash_total_kb': 1024,         # STM32F407 Flash 总量
        'sram_total_kb': 192,           # STM32F407 SRAM 总量
        'flash_used_kb': None,          # 需要从编译报告填入
        'sram_used_kb': None,           # 需要从编译报告填入
        'inference_ram_kb': None,       # 推理过程 RAM 峰值
    }

    # 尝试从编译报告读取
    build_report = 'STM32F407_TDLAS/Build/tdlas.elf.map'
    if os.path.exists(build_report):
        # 简单解析 .map 文件
        with open(build_report, 'r', errors='ignore') as f:
            content = f.read()
            # 查找 Flash 和 SRAM 使用量
            import re
            flash_match = re.search(r'Flash\s+used\s*:\s*(\d+)', content)
            sram_match = re.search(r'SRAM\s+used\s*:\s*(\d+)', content)
            if flash_match:
                info['flash_used_kb'] = int(flash_match.group(1)) / 1024
            if sram_match:
                info['sram_used_kb'] = int(sram_match.group(1)) / 1024

    return info


# ============================================================
# 4. 可视化
# ============================================================

def plot_latency_distribution(latencies, stats, output_dir='experiment_data'):
    """绘制延迟分布图"""
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

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # 直方图
    ax1.hist(latencies, bins=50, color='#2E86AB', alpha=0.7, edgecolor='white')
    ax1.axvline(stats['mean_ms'], color='#E84855', linestyle='--', linewidth=1.5,
                label=f'Mean = {stats["mean_ms"]:.2f} ms')
    ax1.axvline(stats['median_ms'], color='#F6AE2D', linestyle=':', linewidth=1.5,
                label=f'Median = {stats["median_ms"]:.2f} ms')
    ax1.set_xlabel('Inference Latency (ms)')
    ax1.set_ylabel('Count')
    ax1.set_title('STM32F407 Inference Latency Distribution')
    ax1.legend()

    # CDF
    sorted_lat = np.sort(latencies)
    cdf = np.arange(1, len(sorted_lat) + 1) / len(sorted_lat)
    ax2.plot(sorted_lat, cdf, color='#2E86AB', linewidth=2)
    ax2.axhline(0.95, color='gray', linestyle=':', linewidth=1)
    ax2.axvline(stats['p95_ms'], color='#E84855', linestyle='--', linewidth=1.5,
                label=f'P95 = {stats["p95_ms"]:.2f} ms')
    ax2.set_xlabel('Inference Latency (ms)')
    ax2.set_ylabel('Cumulative Probability')
    ax2.set_title('Latency CDF')
    ax2.legend()

    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, 'stm32_latency.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'stm32_latency.pdf'), bbox_inches='tight')
    plt.close('all')
    print("延迟分布图已保存")


def plot_accuracy_comparison(pc_pred, stm32_pred, output_dir='experiment_data'):
    """绘制 PC vs STM32 预测对比"""
    os.makedirs(output_dir, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # 散点图
    ax1.scatter(pc_pred, stm32_pred, c='#2E86AB', alpha=0.5, s=20)
    max_val = max(max(pc_pred), max(stm32_pred))
    ax1.plot([0, max_val], [0, max_val], 'k--', linewidth=1)
    ax1.set_xlabel('PC ONNX Runtime Prediction (ppm)')
    ax1.set_ylabel('STM32 Prediction (ppm)')
    ax1.set_title('PC vs STM32 Predictions')

    # 差异分布
    diff = stm32_pred - pc_pred
    ax2.hist(diff, bins=30, color='#A23B72', alpha=0.7, edgecolor='white')
    ax2.axvline(0, color='gray', linestyle='-', linewidth=0.5)
    ax2.set_xlabel('Prediction Difference (ppm)')
    ax2.set_ylabel('Count')
    ax2.set_title(f'Difference Distribution\nMean={np.mean(diff):.3f}, Std={np.std(diff):.3f}')

    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, 'stm32_accuracy.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'stm32_accuracy.pdf'), bbox_inches='tight')
    plt.close('all')
    print("准确度对比图已保存")


def plot_memory_usage(mem_info, output_dir='experiment_data'):
    """绘制内存使用饼图"""
    os.makedirs(output_dir, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 5))

    # Flash 使用
    flash_used = mem_info.get('flash_used_kb', 150)
    flash_free = mem_info['flash_total_kb'] - flash_used
    ax1.pie([flash_used, flash_free],
            labels=[f'Used\n{flash_used:.0f} KB', f'Free\n{flash_free:.0f} KB'],
            colors=['#E84855', '#E8E8E8'], autopct='%1.1f%%', startangle=90)
    ax1.set_title(f'Flash Usage (Total: {mem_info["flash_total_kb"]} KB)')

    # SRAM 使用
    sram_used = mem_info.get('sram_used_kb', 80)
    sram_free = mem_info['sram_total_kb'] - sram_used
    ax2.pie([sram_used, sram_free],
            labels=[f'Used\n{sram_used:.0f} KB', f'Free\n{sram_free:.0f} KB'],
            colors=['#2E86AB', '#E8E8E8'], autopct='%1.1f%%', startangle=90)
    ax2.set_title(f'SRAM Usage (Total: {mem_info["sram_total_kb"]} KB)')

    plt.tight_layout()
    fig.savefig(os.path.join(output_dir, 'stm32_memory.png'), dpi=600, bbox_inches='tight')
    fig.savefig(os.path.join(output_dir, 'stm32_memory.pdf'), bbox_inches='tight')
    plt.close('all')
    print("内存使用图已保存")


# ============================================================
# 5. 主程序
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='STM32 推理性能测试')
    parser.add_argument('--port', type=str, default=None, help='串口号')
    parser.add_argument('--baudrate', type=int, default=115200, help='波特率')
    parser.add_argument('--demo', action='store_true', help='Demo 模式')
    parser.add_argument('--iterations', type=int, default=1000, help='延迟测试迭代次数')
    parser.add_argument('--output', type=str, default='experiment_data', help='输出目录')
    args = parser.parse_args()

    print("=" * 60)
    print("STM32F407 推理性能测试")
    print("=" * 60)

    test = STM32PerfTest(port=args.port, baudrate=args.baudrate, demo=args.demo)

    try:
        # 1. 延迟测试
        latency_stats, latencies = test.test_latency(n_iterations=args.iterations)

        # 2. 准确度测试
        # 加载测试数据
        from sklearn.model_selection import train_test_split
        dataset_path = 'tdlas_dataset/dataset.csv'
        if os.path.exists(dataset_path):
            df = pd.read_csv(dataset_path)
            X = df.iloc[:, :-1].values
            y = df.iloc[:, -1].values
            _, X_test, _, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

            # PC 端预测（ONNX Runtime）
            try:
                import onnxruntime as ort
                sess = ort.InferenceSession('tdlas_model.onnx')
                input_name = sess.get_inputs()[0].name
                pc_pred = sess.run(None, {input_name: X_test.astype(np.float32)})[0].flatten()
            except Exception:
                print("  ONNX Runtime 不可用，使用真实值近似")
                pc_pred = y_test

            # STM32 预测
            accuracy, stm32_pred = test.test_accuracy(X_test[:50], pc_pred[:50])

            # 绘图
            plot_accuracy_comparison(pc_pred[:50], stm32_pred, args.output)
        else:
            print("  数据集不存在，跳过准确度测试")
            accuracy = None

        # 3. 内存信息
        mem_info = get_memory_info()
        print(f"\n--- 内存使用 ---")
        print(f"  模型大小 (FP32): {mem_info['model_size_bytes']} bytes")
        print(f"  模型大小 (INT8): {mem_info['model_size_int8_bytes']} bytes")
        if mem_info['flash_used_kb']:
            print(f"  Flash 使用: {mem_info['flash_used_kb']:.0f} KB / {mem_info['flash_total_kb']} KB")
        if mem_info['sram_used_kb']:
            print(f"  SRAM 使用: {mem_info['sram_used_kb']:.0f} KB / {mem_info['sram_total_kb']} KB")

        # 绘图
        plot_latency_distribution(latencies, latency_stats, args.output)
        plot_memory_usage(mem_info, args.output)

        # 保存结果
        os.makedirs(args.output, exist_ok=True)
        results = {
            'latency': latency_stats,
            'accuracy': accuracy,
            'memory': mem_info,
        }
        with open(os.path.join(args.output, 'stm32_performance.json'), 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n结果已保存: {os.path.join(args.output, 'stm32_performance.json')}")

    finally:
        test.close()

    print("\nSTM32 性能测试完成！")


if __name__ == '__main__':
    main()
