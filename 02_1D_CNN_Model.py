"""
轻量级 1D-CNN 气体浓度回归模型
================================
输入 TDLAS 的 2f 信号，输出回归预测的气体浓度。
网络结构极度精简，适配 STM32Cube.AI 部署到资源有限的单片机上。
提供完整的 Dataloader 和训练循环，训练完成后导出为 ONNX 格式。

生成时间: Tue May 19 01:27:35 2026
"""

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import os
import json


# ============================================================
# 1. 数据集定义
# ============================================================

class TDLASDataset(Dataset):
    """TDLAS 2f 信号数据集"""

    def __init__(self, signals, concentrations):
        """
        参数:
            signals:       2f 信号矩阵 (N, signal_length)
            concentrations: 浓度数组 (N,)
        """
        self.signals = torch.FloatTensor(signals)
        self.concentrations = torch.FloatTensor(concentrations)

    def __len__(self):
        return len(self.signals)

    def __getitem__(self, idx):
        # 添加通道维度: (signal_length,) -> (1, signal_length)
        signal = self.signals[idx].unsqueeze(0)
        conc = self.concentrations[idx]
        return signal, conc


def load_dataset(csv_path, test_size=0.2, batch_size=32):
    """
    加载 CSV 数据集并创建 DataLoader。

    参数:
        csv_path:   CSV 文件路径
        test_size:  测试集比例
        batch_size: 批大小
    返回:
        train_loader, test_loader, scaler (用于反归一化)
    """
    df = pd.read_csv(csv_path)

    # 提取信号列和浓度列
    signal_cols = [c for c in df.columns if c.startswith("signal_")]
    X = df[signal_cols].values.astype(np.float32)
    y = df["concentration_ppm"].values.astype(np.float32)

    # 标准化信号 (有助于训练收敛)
    scaler = StandardScaler()
    X = scaler.fit_transform(X)

    # 浓度归一化到 [0, 1] 范围 (便于网络学习)
    y_max = y.max()
    y_normalized = y / y_max

    # 划分训练集/测试集
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_normalized, test_size=test_size, random_state=42
    )

    train_dataset = TDLASDataset(X_train, y_train)
    test_dataset = TDLASDataset(X_test, y_test)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

    return train_loader, test_loader, scaler, y_max


# ============================================================
# 2. 轻量级 1D-CNN 模型（专为 STM32 部署优化）
# ============================================================

class TinyTDLASNet(nn.Module):
    """
    极简 1D-CNN 用于 TDLAS 浓度回归。

    设计原则:
    - 参数量 < 10K（适配 STM32F4/F7 的 Flash 和 RAM）
    - 仅使用 Conv1D + ReLU + GlobalAvgPool + Linear
    - 无 BatchNorm（STM32Cube.AI 部分版本支持有限）
    - 无 Dropout（推理时不需要）
    - 输入: (batch, 1, 512) — 512 点 2f 信号
    - 输出: (batch, 1) — 归一化浓度
    """

    def __init__(self, signal_length=512):
        super().__init__()

        # 特征提取器: 3 层 1D 卷积逐步降低维度
        self.features = nn.Sequential(
            # Conv Block 1: 1 -> 8 通道, k=7, 压缩 4x
            nn.Conv1d(in_channels=1, out_channels=8, kernel_size=7, stride=4, padding=3),
            nn.ReLU(inplace=True),

            # Conv Block 2: 8 -> 16 通道, k=5, 压缩 4x
            nn.Conv1d(in_channels=8, out_channels=16, kernel_size=5, stride=4, padding=2),
            nn.ReLU(inplace=True),

            # Conv Block 3: 16 -> 32 通道, k=3, 压缩 2x
            nn.Conv1d(in_channels=16, out_channels=32, kernel_size=3, stride=2, padding=1),
            nn.ReLU(inplace=True),
        )

        # 全局平均池化: 将时序维度压缩为 1
        self.global_pool = nn.AdaptiveAvgPool1d(1)

        # 回归头: 极简 2 层全连接
        self.regressor = nn.Sequential(
            nn.Linear(32, 16),
            nn.ReLU(inplace=True),
            nn.Linear(16, 1),
            nn.Sigmoid(),  # 输出 [0, 1] 对应归一化浓度
        )

    def forward(self, x):
        """
        前向传播。
        输入 x: (batch, 1, signal_length)
        输出:   (batch, 1)
        """
        x = self.features(x)       # (batch, 32, ~16)
        x = self.global_pool(x)    # (batch, 32, 1)
        x = x.squeeze(-1)          # (batch, 32)
        x = self.regressor(x)      # (batch, 1)
        return x

    def count_parameters(self):
        """统计可训练参数量"""
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


# ============================================================
# 3. 训练循环
# ============================================================

def train_model(model, train_loader, test_loader, y_max,
                epochs=100, lr=1e-3, device="cpu"):
    """
    训练模型。

    参数:
        model:        TinyTDLASNet 实例
        train_loader: 训练数据加载器
        test_loader:  测试数据加载器
        y_max:        浓度最大值（用于反归一化计算真实误差）
        epochs:       训练轮数
        lr:           学习率
        device:       训练设备
    """
    model = model.to(device)
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_test_loss = float("inf")
    best_epoch = 0
    history = {"train_loss": [], "test_loss": [], "test_mae_ppm": []}

    print(f"\n模型参数量: {model.count_parameters():,}")
    print(f"训练设备: {device}")
    print("=" * 60)

    for epoch in range(epochs):
        # ---------- 训练阶段 ----------
        model.train()
        train_loss_sum = 0.0
        n_train = 0

        for signals, targets in train_loader:
            signals = signals.to(device)
            targets = targets.to(device).unsqueeze(1)

            optimizer.zero_grad()
            outputs = model(signals)
            loss = criterion(outputs, targets)
            loss.backward()
            optimizer.step()

            train_loss_sum += loss.item() * len(signals)
            n_train += len(signals)

        train_loss = train_loss_sum / n_train

        # ---------- 测试阶段 ----------
        model.eval()
        test_loss_sum = 0.0
        mae_sum = 0.0
        n_test = 0

        with torch.no_grad():
            for signals, targets in test_loader:
                signals = signals.to(device)
                targets = targets.to(device).unsqueeze(1)

                outputs = model(signals)
                loss = criterion(outputs, targets)

                test_loss_sum += loss.item() * len(signals)
                # 计算真实浓度的 MAE (ppm)
                mae = torch.abs(outputs * y_max - targets * y_max).sum().item()
                mae_sum += mae
                n_test += len(signals)

        test_loss = test_loss_sum / n_test
        test_mae = mae_sum / n_test

        scheduler.step()

        history["train_loss"].append(train_loss)
        history["test_loss"].append(test_loss)
        history["test_mae_ppm"].append(test_mae)

        # 保存最佳模型
        if test_loss < best_test_loss:
            best_test_loss = test_loss
            best_epoch = epoch + 1
            torch.save(model.state_dict(), "best_tdlas_model.pth")

        # 打印进度
        if (epoch + 1) % 10 == 0 or epoch == 0:
            print(
                f"Epoch [{epoch+1:3d}/{epochs}]  "
                f"Train Loss: {train_loss:.6f}  "
                f"Test Loss: {test_loss:.6f}  "
                f"Test MAE: {test_mae:.1f} ppm  "
                f"LR: {scheduler.get_last_lr()[0]:.6f}"
            )

    print("=" * 60)
    print(f"训练完成！最佳模型在 Epoch {best_epoch}，Test Loss: {best_test_loss:.6f}")

    # 保存训练历史
    with open("training_history.json", "w") as f:
        json.dump(history, f, indent=2)

    return history


# ============================================================
# 4. ONNX 导出
# ============================================================

def export_to_onnx(model, signal_length=512, onnx_path="tdlas_model.onnx"):
    """
    将训练好的模型导出为 ONNX 格式（适配 STM32Cube.AI）。

    参数:
        model:        训练好的模型
        signal_length: 信号长度
        onnx_path:    ONNX 文件输出路径
    """
    model.eval()

    # 创建与训练时相同形状的 dummy 输入
    # STM32Cube.AI 要求固定 batch_size=1 的输入
    dummy_input = torch.randn(1, 1, signal_length)

    # 导出
    torch.onnx.export(
        model,
        dummy_input,
        onnx_path,
        export_params=True,
        opset_version=11,               # STM32Cube.AI 兼容的 opset 版本
        do_constant_folding=True,
        input_names=["input_2f_signal"],
        output_names=["concentration_normalized"],
        dynamic_axes=None,               # STM32 部署用固定尺寸
    )

    print(f"\nONNX 模型已导出: {onnx_path}")

    # 验证 ONNX 模型
    try:
        import onnx
        onnx_model = onnx.load(onnx_path)
        onnx.checker.check_model(onnx_model)
        print("ONNX 模型验证通过!")

        # 打印模型大小
        file_size = os.path.getsize(onnx_path) / 1024
        print(f"模型文件大小: {file_size:.1f} KB")

        # 打印输入输出信息
        print(f"  输入: {onnx_model.graph.input[0].name}  shape: [1, 1, {signal_length}]")
        print(f"  输出: {onnx_model.graph.output[0].name}  shape: [1, 1]")
    except ImportError:
        print("提示: 安装 onnx 库可验证模型 (pip install onnx)")

    return onnx_path


def export_quantized_onnx(model, train_loader, signal_length=512,
                          onnx_path="tdlas_model_int8.onnx"):
    """
    导出 INT8 量化版本的 ONNX 模型（进一步减小 STM32 部署体积和推理时间）。
    使用动态量化将 Float32 权重转为 INT8。

    参数:
        model:        训练好的模型
        train_loader: 训练数据（用于校准量化参数）
        signal_length: 信号长度
        onnx_path:    量化 ONNX 文件输出路径
    """
    model.eval()

    # PyTorch 动态量化
    quantized_model = torch.quantization.quantize_dynamic(
        model,
        {nn.Linear},       # 量化全连接层
        dtype=torch.qint8
    )

    dummy_input = torch.randn(1, 1, signal_length)
    torch.onnx.export(
        quantized_model,
        dummy_input,
        onnx_path,
        export_params=True,
        opset_version=11,
        input_names=["input_2f_signal"],
        output_names=["concentration_normalized"],
    )

    file_size = os.path.getsize(onnx_path) / 1024
    print(f"INT8 量化 ONNX 模型已导出: {onnx_path} ({file_size:.1f} KB)")

    return onnx_path


# ============================================================
# 5. 主程序
# ============================================================

if __name__ == "__main__":
    # 配置
    CSV_PATH = "tdlas_dataset/tdlas_2f_dataset.csv"
    SIGNAL_LENGTH = 512
    BATCH_SIZE = 32
    EPOCHS = 100
    LEARNING_RATE = 1e-3
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

    print("=" * 60)
    print("TDLAS 1D-CNN 浓度回归模型训练")
    print("=" * 60)

    # 检查数据集是否存在
    if not os.path.exists(CSV_PATH):
        print(f"错误: 数据集文件不存在 ({CSV_PATH})")
        print("请先运行 01_TDLAS_Simulation.py 生成数据集。")
        exit(1)

    # 加载数据
    print(f"\n加载数据集: {CSV_PATH}")
    train_loader, test_loader, scaler, y_max = load_dataset(
        CSV_PATH, test_size=0.2, batch_size=BATCH_SIZE
    )
    print(f"  训练集样本数: {len(train_loader.dataset)}")
    print(f"  测试集样本数: {len(test_loader.dataset)}")
    print(f"  浓度最大值: {y_max} ppm")

    # 创建模型
    model = TinyTDLASNet(signal_length=SIGNAL_LENGTH)
    print(f"\n模型结构:\n{model}")

    # 训练
    history = train_model(
        model, train_loader, test_loader, y_max,
        epochs=EPOCHS, lr=LEARNING_RATE, device=DEVICE
    )

    # 加载最佳模型
    model.load_state_dict(torch.load("best_tdlas_model.pth", weights_only=True))

    # 导出 ONNX
    export_to_onnx(model, SIGNAL_LENGTH, "tdlas_model.onnx")

    # 导出量化版本
    export_quantized_onnx(model, train_loader, SIGNAL_LENGTH, "tdlas_model_int8.onnx")

    print("\n全部完成！生成文件:")
    print("  - best_tdlas_model.pth    (PyTorch 最佳权重)")
    print("  - tdlas_model.onnx        (ONNX 模型)")
    print("  - tdlas_model_int8.onnx   (INT8 量化 ONNX)")
    print("  - training_history.json   (训练历史)")
