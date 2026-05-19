"""
串口数据清洗与 Origin 格式化工具
=================================
读取串口传来的单片机预测浓度和原始 2f 信号。
进行基本的数字滤波。
自动将数据格式化为 Origin 软件兼容的标准矩阵形式。

生成时间: Tue May 19 01:27:44 2026
"""

import serial
import serial.tools.list_ports
import numpy as np
import pandas as pd
import struct
import time
import os
import sys
from collections import deque
from datetime import datetime


# ============================================================
# 1. 串口配置与连接
# ============================================================

class SerialConfig:
    """串口连接参数"""
    def __init__(self, port=None, baudrate=115200, timeout=1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout


def list_available_ports():
    """列出所有可用的串口设备"""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("未检测到串口设备。")
        return []
    print("\n可用串口设备:")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device} - {p.description}")
    return ports


def auto_select_port():
    """自动选择串口（优先选择包含 USB/CH340/CP2102 的设备）"""
    ports = serial.tools.list_ports.comports()
    if not ports:
        return None

    keywords = ["USB", "CH340", "CP2102", "FTDI", "STLink", "Virtual COM"]
    for p in ports:
        for kw in keywords:
            if kw.lower() in p.description.lower():
                return p.device

    # 没找到关键字，返回第一个
    return ports[0].device


def open_serial(config):
    """
    打开串口连接。

    参数:
        config: SerialConfig 对象
    返回:
        serial.Serial 对象
    """
    if config.port is None:
        config.port = auto_select_port()
        if config.port is None:
            print("错误: 未找到可用串口设备。")
            sys.exit(1)

    print(f"\n正在连接串口: {config.port} @ {config.baudrate} bps")
    try:
        ser = serial.Serial(
            port=config.port,
            baudrate=config.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=config.timeout,
        )
        time.sleep(0.1)  # 等待串口稳定
        ser.reset_input_buffer()
        print(f"串口连接成功: {ser.name}")
        return ser
    except serial.SerialException as e:
        print(f"串口连接失败: {e}")
        sys.exit(1)


# ============================================================
# 2. 数据帧解析
# ============================================================

FRAME_HEADER = b'\xAA\x55'
FRAME_TAIL   = b'\x0D\x0A'


def parse_binary_frame(data):
    """
    解析二进制数据帧。

    帧格式: [0xAA 0x55] [长度 2B] [ADC 数据 N*2B] [0x0D 0x0A]

    参数:
        data: 原始字节数据
    返回:
        解析出的 ADC 数据列表，解析消耗的字节数
    """
    # 查找帧头
    idx = data.find(FRAME_HEADER)
    if idx == -1:
        return None, len(data)

    # 至少需要: 帧头(2) + 长度(2) + 最小数据(1) + 帧尾(2) = 7 字节
    if len(data) < idx + 7:
        return None, idx

    # 解析数据长度
    payload_len = struct.unpack_from('<H', data, idx + 2)[0]

    # 检查是否有足够数据
    frame_total = 2 + 2 + payload_len * 2 + 2  # 帧头 + 长度 + 数据 + 帧尾
    if len(data) < idx + frame_total:
        return None, idx

    # 检查帧尾
    tail_pos = idx + 2 + 2 + payload_len * 2
    if data[tail_pos:tail_pos + 2] != FRAME_TAIL:
        # 帧尾不匹配，跳过这个帧头继续搜索
        return parse_binary_frame(data[idx + 2:])

    # 解析 ADC 数据
    adc_values = []
    for i in range(payload_len):
        val = struct.unpack_from('<H', data, idx + 4 + i * 2)[0]
        adc_values.append(val)

    consumed = idx + frame_total
    return adc_values, consumed


def parse_ascii_line(line):
    """
    解析 ASCII 格式的 ADC 数据行。

    格式: "ADC: 2048"

    参数:
        line: 字符串行
    返回:
        ADC 值 (int) 或 None
    """
    line = line.strip()
    if line.startswith("ADC:"):
        try:
            return int(line.split(":")[1].strip())
        except (ValueError, IndexError):
            return None
    return None


# ============================================================
# 3. 数字滤波器
# ============================================================

class MovingAverageFilter:
    """滑动平均滤波器 — 简单有效地平滑高频噪声"""

    def __init__(self, window_size=5):
        self.window_size = window_size
        self.buffer = deque(maxlen=window_size)

    def process(self, value):
        self.buffer.append(value)
        return sum(self.buffer) / len(self.buffer)

    def process_array(self, arr):
        """对整个数组进行滑动平均"""
        kernel = np.ones(self.window_size) / self.window_size
        # 使用 'same' 模式保持数组长度不变
        return np.convolve(arr, kernel, mode='same')


class MedianFilter:
    """中值滤波器 — 有效去除脉冲噪声（尖峰）"""

    def __init__(self, window_size=5):
        self.window_size = window_size
        self.buffer = deque(maxlen=window_size)

    def process(self, value):
        self.buffer.append(value)
        return float(np.median(list(self.buffer)))

    def process_array(self, arr, kernel_size=None):
        """对数组进行中值滤波"""
        k = kernel_size or self.window_size
        half = k // 2
        result = arr.copy()
        for i in range(half, len(arr) - half):
            result[i] = np.median(arr[i - half:i + half + 1])
        return result


class ButterworthLowpassFilter:
    """
    Butterworth 低通滤波器 — 平滑信号同时保留有用的低频特征。

    不使用 scipy 依赖，用简单的二阶 IIR 滤波实现。
    """

    def __init__(self, cutoff_ratio=0.1):
        """
        参数:
            cutoff_ratio: 截止频率与采样频率的比值 (0-0.5)
                          0.1 表示截止频率为采样率的 10%
        """
        self.cutoff_ratio = cutoff_ratio
        # 预计算滤波器系数（二阶 Butterworth）
        omega = 2.0 * np.pi * cutoff_ratio
        sin_omega = np.sin(omega)
        cos_omega = np.cos(omega)
        alpha = sin_omega / 2.0 * np.sqrt(2.0)  # Q = 1/sqrt(2) for Butterworth

        self.b0 = (1.0 - cos_omega) / 2.0
        self.b1 = 1.0 - cos_omega
        self.b2 = (1.0 - cos_omega) / 2.0
        self.a0 = 1.0 + alpha
        self.a1 = -2.0 * cos_omega
        self.a2 = 1.0 - alpha

        # 归一化系数
        self.b0 /= self.a0
        self.b1 /= self.a0
        self.b2 /= self.a0
        self.a1 /= self.a0
        self.a2 /= self.a0

        # 状态变量（用于流式处理）
        self.x_prev = [0.0, 0.0]
        self.y_prev = [0.0, 0.0]

    def process(self, x):
        """流式处理单个采样点"""
        y = self.b0 * x + self.b1 * self.x_prev[0] + self.b2 * self.x_prev[1] \
            - self.a1 * self.y_prev[0] - self.a2 * self.y_prev[1]

        # 更新状态
        self.x_prev[1] = self.x_prev[0]
        self.x_prev[0] = x
        self.y_prev[1] = self.y_prev[0]
        self.y_prev[0] = y

        return y

    def process_array(self, arr):
        """对整个数组进行双向滤波（零相移）"""
        # 前向滤波
        forward = np.zeros_like(arr, dtype=float)
        x_prev = [0.0, 0.0]
        y_prev = [0.0, 0.0]
        for i, x in enumerate(arr):
            y = self.b0 * x + self.b1 * x_prev[0] + self.b2 * x_prev[1] \
                - self.a1 * y_prev[0] - self.a2 * y_prev[1]
            forward[i] = y
            x_prev[1] = x_prev[0]; x_prev[0] = x
            y_prev[1] = y_prev[0]; y_prev[0] = y

        # 反向滤波（消除相移）
        result = np.zeros_like(arr, dtype=float)
        x_prev = [0.0, 0.0]
        y_prev = [0.0, 0.0]
        for i in range(len(forward) - 1, -1, -1):
            x = forward[i]
            y = self.b0 * x + self.b1 * x_prev[0] + self.b2 * x_prev[1] \
                - self.a1 * y_prev[0] - self.a2 * y_prev[1]
            result[i] = y
            x_prev[1] = x_prev[0]; x_prev[0] = x
            y_prev[1] = y_prev[0]; y_prev[0] = y

        return result


def remove_outliers(data, threshold=3.0):
    """
    基于 Z-score 去除离群值。

    参数:
        data:      输入数组
        threshold: Z-score 阈值（默认 3 倍标准差）
    返回:
        替换离群值后的数组
    """
    result = data.copy()
    mean = np.mean(data)
    std = np.std(data)
    if std == 0:
        return result
    z_scores = np.abs((data - mean) / std)
    outliers = z_scores > threshold
    # 用中值替换离群值
    median_val = np.median(data)
    result[outliers] = median_val
    return result


# ============================================================
# 4. Origin 格式输出
# ============================================================

class OriginFormatter:
    """
    将数据格式化为 Origin 软件兼容的格式。

    Origin 导入规范:
    - 第一行为列标题（Long Name / Units）
    - 后续行为数据
    - 制表符或逗号分隔
    - 数值格式: 固定小数点或科学计数法
    """

    @staticmethod
    def save_as_csv(df, filepath, title_row=True):
        """
        保存为 Origin 兼容的 CSV 文件。

        Origin 导入 CSV 的规范:
        - 第 1 行: 列名（变量名称）
        - 第 2 行: 列注释/单位（可选，以 // 开头）
        - 第 3 行起: 数据
        - 分隔符: 逗号或制表符
        """
        with open(filepath, 'w', encoding='utf-8') as f:
            # 第 1 行: 列标题
            headers = list(df.columns)
            f.write(','.join(headers) + '\n')

            # 第 2 行: 单位/注释行
            units = []
            for col in headers:
                if 'time' in col.lower():
                    units.append('s')
                elif 'voltage' in col.lower() or col.lower().startswith('v_'):
                    units.append('V')
                elif 'current' in col.lower():
                    units.append('mA')
                elif 'concentration' in col.lower() or 'conc' in col.lower():
                    units.append('ppm')
                elif 'signal' in col.lower():
                    units.append('a.u.')
                elif 'frequency' in col.lower() or 'freq' in col.lower():
                    units.append('Hz')
                else:
                    units.append('')
            f.write('// ' + ','.join(units) + '\n')

            # 数据行
            for _, row in df.iterrows():
                values = []
                for val in row:
                    if isinstance(val, float):
                        values.append(f"{val:.6f}")
                    else:
                        values.append(str(val))
                f.write(','.join(values) + '\n')

        print(f"  Origin CSV 已保存: {filepath}")

    @staticmethod
    def save_as_matrix(signals, filepath, x_axis=None, col_prefix="S"):
        """
        保存为 Origin 矩阵格式（适合绘制热图/等高线图）。

        参数:
            signals:    2D 数组 (n_samples, n_points)
            filepath:   输出文件路径
            x_axis:     X 轴数据（如频率或时间数组）
            col_prefix: 列名前缀
        """
        n_samples, n_points = signals.shape

        with open(filepath, 'w', encoding='utf-8') as f:
            # 列标题
            if x_axis is not None:
                headers = ['X'] + [f'{col_prefix}_{i}' for i in range(n_samples)]
            else:
                headers = [f'{col_prefix}_{i}' for i in range(n_samples)]
            f.write('\t'.join(headers) + '\n')

            # 单位行
            units = ['index'] + ['a.u.'] * n_samples
            f.write('// ' + '\t'.join(units) + '\n')

            # 数据矩阵 (转置: 每行是一个采样点在不同样本中的值)
            for j in range(n_points):
                row_vals = []
                if x_axis is not None:
                    row_vals.append(f"{x_axis[j]:.4f}")
                for i in range(n_samples):
                    row_vals.append(f"{signals[i, j]:.6f}")
                f.write('\t'.join(row_vals) + '\n')

        print(f"  Origin 矩阵已保存: {filepath}")

    @staticmethod
    def save_concentration_comparison(time_stamps, concentrations, filepath,
                                      predicted=None):
        """
        保存浓度对比数据（实测 vs 预测），适合 Origin 双 Y 轴图。

        参数:
            time_stamps:   时间戳数组
            concentrations: 浓度数组
            filepath:      输出文件路径
            predicted:     预测浓度数组（可选）
        """
        data = {
            'Time_s': time_stamps,
            'Measured_ppm': concentrations,
        }
        if predicted is not None:
            data['Predicted_ppm'] = predicted
            data['Error_ppm'] = np.array(concentrations) - np.array(predicted)

        df = pd.DataFrame(data)
        OriginFormatter.save_as_csv(df, filepath)


# ============================================================
# 5. 数据采集与处理主流程
# ============================================================

class TDLASDataCollector:
    """TDLAS 数据采集器 — 从串口读取、滤波、格式化输出"""

    def __init__(self, serial_config, output_dir="origin_output",
                 filter_window=5, butterworth_cutoff=0.1):
        """
        参数:
            serial_config:      串口配置
            output_dir:         输出目录
            filter_window:      滑动平均窗口大小
            butterworth_cutoff: Butterworth 低通截止频率比率
        """
        self.serial_config = serial_config
        self.output_dir = output_dir
        self.ser = None

        # 滤波器
        self.ma_filter = MovingAverageFilter(filter_window)
        self.median_filter = MedianFilter(filter_window)
        self.bw_filter = ButterworthLowpassFilter(butterworth_cutoff)

        # 数据存储
        self.raw_signals = []         # 原始 2f 信号
        self.filtered_signals = []    # 滤波后的信号
        self.concentrations = []      # MCU 预测浓度
        self.timestamps = []          # 时间戳
        self.frame_count = 0          # 帧计数

        # Origin 格式化器
        self.formatter = OriginFormatter()

        os.makedirs(output_dir, exist_ok=True)

    def collect(self, duration_seconds=60, max_frames=None):
        """
        从串口采集数据。

        参数:
            duration_seconds: 采集时长（秒），0 表示无限采集
            max_frames:       最大帧数，None 表示不限制
        """
        self.ser = open_serial(self.serial_config)
        buffer = bytearray()
        start_time = time.time()

        print(f"\n开始数据采集（时长: {duration_seconds}s）...")
        print("按 Ctrl+C 停止采集\n")

        try:
            while True:
                # 检查停止条件
                elapsed = time.time() - start_time
                if duration_seconds > 0 and elapsed > duration_seconds:
                    break
                if max_frames and self.frame_count >= max_frames:
                    break

                # 读取串口数据
                bytes_available = self.ser.in_waiting
                if bytes_available > 0:
                    buffer.extend(self.ser.read(bytes_available))
                else:
                    time.sleep(0.001)  # 避免 CPU 空转
                    continue

                # 尝试解析数据帧
                while len(buffer) >= 7:  # 最小帧长度
                    adc_data, consumed = parse_binary_frame(buffer)
                    if adc_data is not None:
                        self._process_frame(adc_data)
                        buffer = buffer[consumed:]
                    elif consumed > 0:
                        buffer = buffer[consumed:]
                    else:
                        break

        except KeyboardInterrupt:
            print("\n用户中断采集。")
        finally:
            self.ser.close()
            print(f"\n采集结束。共接收 {self.frame_count} 帧数据。")

    def _process_frame(self, adc_data):
        """处理一帧 ADC 数据"""
        raw = np.array(adc_data, dtype=np.float64)

        # 1. 去除离群值
        cleaned = remove_outliers(raw, threshold=3.0)

        # 2. 中值滤波（去脉冲噪声）
        median_filtered = self.median_filter.process_array(cleaned)

        # 3. Butterworth 低通滤波（平滑）
        filtered = self.bw_filter.process_array(median_filtered)

        # 存储
        self.raw_signals.append(raw)
        self.filtered_signals.append(filtered)
        self.timestamps.append(time.time())

        self.frame_count += 1

        # 进度显示
        if self.frame_count % 10 == 0:
            print(f"  已采集 {self.frame_count} 帧 | "
                  f"最新均值: {np.mean(filtered):.1f} | "
                  f"最新标准差: {np.std(filtered):.2f}")

    def export_to_origin(self):
        """
        将采集的数据导出为 Origin 兼容格式。

        生成的文件:
        1. raw_signals.csv       — 原始信号矩阵 (Origin XY 数据)
        2. filtered_signals.csv  — 滤波后信号矩阵
        3. signal_stats.csv      — 每帧统计量 (均值、标准差、峰峰值)
        4. comparison.csv        — 原始 vs 滤波 对比
        """
        if not self.raw_signals:
            print("没有数据可导出。")
            return

        print(f"\n导出数据到 Origin 格式 ({self.output_dir}/)...")

        # 相对时间
        t0 = self.timestamps[0]
        rel_time = np.array([t - t0 for t in self.timestamps])

        # 信号矩阵
        raw_matrix = np.array(self.raw_signals)
        filt_matrix = np.array(self.filtered_signals)
        n_frames, n_points = raw_matrix.shape

        # X 轴: 采样点索引
        x_axis = np.arange(n_points)

        # ---- 文件 1: 原始信号矩阵 ----
        self.formatter.save_as_matrix(
            raw_matrix,
            os.path.join(self.output_dir, "raw_signals.csv"),
            x_axis=x_axis,
            col_prefix="Raw"
        )

        # ---- 文件 2: 滤波后信号矩阵 ----
        self.formatter.save_as_matrix(
            filt_matrix,
            os.path.join(self.output_dir, "filtered_signals.csv"),
            x_axis=x_axis,
            col_prefix="Filt"
        )

        # ---- 文件 3: 每帧统计量 ----
        stats_data = {
            'Time_s': [f"{t:.3f}" for t in rel_time],
            'Raw_Mean': [np.mean(s) for s in self.raw_signals],
            'Raw_Std': [np.std(s) for s in self.raw_signals],
            'Raw_PeakToPeak': [np.ptp(s) for s in self.raw_signals],
            'Filtered_Mean': [np.mean(s) for s in self.filtered_signals],
            'Filtered_Std': [np.std(s) for s in self.filtered_signals],
            'Filtered_PeakToPeak': [np.ptp(s) for s in self.filtered_signals],
        }
        stats_df = pd.DataFrame(stats_data)
        self.formatter.save_as_csv(
            stats_df,
            os.path.join(self.output_dir, "signal_stats.csv")
        )

        # ---- 文件 4: 原始 vs 滤波对比 (取第 1 帧为例) ----
        if n_frames > 0:
            compare_data = {
                'Sample_Index': x_axis,
                'Raw_Signal': raw_matrix[0],
                'Filtered_Signal': filt_matrix[0],
                'Difference': raw_matrix[0] - filt_matrix[0],
            }
            compare_df = pd.DataFrame(compare_data)
            self.formatter.save_as_csv(
                compare_df,
                os.path.join(self.output_dir, "comparison_frame0.csv")
            )

        print(f"\n导出完成！共 {n_frames} 帧 x {n_points} 点。")
        print(f"文件列表:")
        print(f"  1. raw_signals.csv       — 原始信号矩阵")
        print(f"  2. filtered_signals.csv  — 滤波后信号矩阵")
        print(f"  3. signal_stats.csv      — 统计量 (均值/标准差/峰峰值)")
        print(f"  4. comparison_frame0.csv — 帧0 对比 (原始 vs 滤波)")


# ============================================================
# 6. 演示模式 (无硬件时使用)
# ============================================================

def demo_mode():
    """
    演示模式: 使用模拟数据演示完整的数据处理流程。
    适用于没有实际硬件连接时验证代码功能。
    """
    print("=" * 60)
    print("演示模式 — 使用模拟数据")
    print("=" * 60)

    np.random.seed(42)

    # 模拟参数
    n_frames = 50
    n_points = 256
    output_dir = "origin_output"

    formatter = OriginFormatter()
    ma_filter = MovingAverageFilter(5)
    bw_filter = ButterworthLowpassFilter(0.1)
    median_filter = MedianFilter(5)

    os.makedirs(output_dir, exist_ok=True)

    # 生成模拟数据
    raw_signals = []
    filtered_signals = []
    timestamps = []
    concentrations = []

    print(f"\n生成 {n_frames} 帧模拟数据...")

    for i in range(n_frames):
        t = i * 0.1  # 时间步 0.1s

        # 模拟 2f 信号: Lorentzian 二阶导数形状 + 噪声
        x = np.linspace(-3, 3, n_points)
        # 纯净 2f 信号 (二阶导数 Lorentzian)
        clean_2f = -(2 * x**2 - 1) / (1 + x**2)**2
        # 添加高斯白噪声
        noise = np.random.normal(0, 0.15, n_points)
        # 添加随机脉冲噪声
        if np.random.random() > 0.7:
            pulse_idx = np.random.randint(0, n_points, 3)
            noise[pulse_idx] += np.random.uniform(-1, 1, 3) * 0.5

        raw = clean_2f + noise

        # 滤波处理
        cleaned = remove_outliers(raw)
        med = median_filter.process_array(cleaned)
        filtered = bw_filter.process_array(med)

        raw_signals.append(raw)
        filtered_signals.append(filtered)
        timestamps.append(t)

        # 模拟浓度 (缓慢变化)
        conc = 500 + 200 * np.sin(2 * np.pi * 0.05 * t) + np.random.normal(0, 10)
        concentrations.append(max(0, conc))

    raw_matrix = np.array(raw_signals)
    filt_matrix = np.array(filtered_signals)

    # 导出
    x_axis = np.arange(n_points)
    t_axis = np.array(timestamps)

    print("\n导出 Origin 格式文件...")

    # 1. 原始信号矩阵
    formatter.save_as_matrix(raw_matrix, os.path.join(output_dir, "demo_raw.csv"),
                             x_axis=x_axis, col_prefix="Raw")

    # 2. 滤波后信号矩阵
    formatter.save_as_matrix(filt_matrix, os.path.join(output_dir, "demo_filtered.csv"),
                             x_axis=x_axis, col_prefix="Filt")

    # 3. 统计量
    stats_df = pd.DataFrame({
        'Time_s': [f"{t:.1f}" for t in timestamps],
        'Raw_Mean': [np.mean(s) for s in raw_signals],
        'Raw_Std': [np.std(s) for s in raw_signals],
        'Raw_PeakToPeak': [np.ptp(s) for s in raw_signals],
        'Filt_Mean': [np.mean(s) for s in filtered_signals],
        'Filt_Std': [np.std(s) for s in filtered_signals],
        'Filt_PeakToPeak': [np.ptp(s) for s in filtered_signals],
        'Concentration_ppm': [f"{c:.1f}" for c in concentrations],
    })
    formatter.save_as_csv(stats_df, os.path.join(output_dir, "demo_stats.csv"))

    # 4. 浓度随时间变化 (Origin 折线图数据)
    formatter.save_concentration_comparison(
        timestamps, concentrations,
        os.path.join(output_dir, "demo_concentration.csv"),
        predicted=[c + np.random.normal(0, 15) for c in concentrations]
    )

    print("\n演示完成！请在 Origin 中打开 origin_output/ 目录下的文件。")


# ============================================================
# 7. 主程序入口
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TDLAS 串口数据清洗与 Origin 格式化工具")
    parser.add_argument('--port', type=str, default=None, help='串口号 (如 COM3)')
    parser.add_argument('--baudrate', type=int, default=115200, help='波特率')
    parser.add_argument('--duration', type=int, default=60, help='采集时长 (秒)')
    parser.add_argument('--output', type=str, default='origin_output', help='输出目录')
    parser.add_argument('--demo', action='store_true', help='使用模拟数据演示')
    parser.add_argument('--list', action='store_true', help='列出可用串口')
    args = parser.parse_args()

    if args.list:
        list_available_ports()
        sys.exit(0)

    if args.demo:
        demo_mode()
    else:
        config = SerialConfig(port=args.port, baudrate=args.baudrate)
        collector = TDLASDataCollector(config, output_dir=args.output)
        collector.collect(duration_seconds=args.duration)
        collector.export_to_origin()
