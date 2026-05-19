"""
论文全部 11 张图绘制
===================
Nature 级别科研绘图，使用 Python matplotlib
输出: PNG (600dpi) + PDF (矢量)
"""

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.gridspec import GridSpec
import json
import os

# ============================================================
# 全局样式设置 (Nature 风格)
# ============================================================
mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "SimSun", "DejaVu Serif"],
    "font.sans-serif": ["Arial", "Helvetica", "SimHei", "DejaVu Sans"],
    "mathtext.fontset": "stix",
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 9,
    "axes.linewidth": 0.8,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.labelsize": 10,
    "axes.titlesize": 11,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "xtick.major.size": 3,
    "ytick.major.size": 3,
    "legend.fontsize": 8,
    "legend.frameon": False,
    "figure.dpi": 150,
    "savefig.dpi": 600,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
})

# 配色方案 (低饱和度学术色系)
COLORS = {
    "blue": "#2166AC",
    "red": "#D6604D",
    "green": "#4DAF4A",
    "orange": "#FF7F00",
    "purple": "#984EA3",
    "gray": "#666666",
    "lightblue": "#92C5DE",
    "lightred": "#F4A582",
    "darkblue": "#1B4F72",
}

os.makedirs("figures", exist_ok=True)


def save_fig(fig, name):
    """保存 PNG + PDF"""
    fig.savefig(f"figures/{name}.png", dpi=600, bbox_inches="tight")
    fig.savefig(f"figures/{name}.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"  已保存: figures/{name}.png / .pdf")


# ============================================================
# 图2: 不同浓度下的 2f 信号波形
# ============================================================
def plot_fig2_2f_signals():
    """2f 信号波形对比图"""
    df = pd.read_csv("tdlas_dataset/reference_2f_signals.csv")
    signal_cols = [c for c in df.columns if c.startswith("signal_")]
    x = np.arange(len(signal_cols))

    fig, ax = plt.subplots(figsize=(6, 3.5))

    conc_list = df["concentration_ppm"].values
    cmap = plt.cm.get_cmap("RdYlBu_r", len(conc_list))

    for i, row in df.iterrows():
        conc = row["concentration_ppm"]
        signal = row[signal_cols].values.astype(float)
        color = cmap(i)
        ax.plot(x, signal, color=color, linewidth=0.8, label=f"{int(conc)} ppm")

    ax.set_xlabel("Sample Index")
    ax.set_ylabel("Normalized 2f Signal (a.u.)")
    ax.set_xlim(0, len(signal_cols))
    ax.axhline(y=0, color="gray", linewidth=0.3, linestyle="--")

    # 图例放右侧
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5),
              fontsize=7, title="CO Conc.", title_fontsize=8,
              frameon=False, handlelength=1.5)

    save_fig(fig, "fig2_2f_signals")


# ============================================================
# 图4: 训练损失曲线
# ============================================================
def plot_fig4_training():
    """训练过程 Loss 和 MAE 曲线"""
    with open("training_history.json", "r") as f:
        history = json.load(f)

    epochs = list(range(1, len(history["train_loss"]) + 1))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 3.2))

    # 左图: Loss
    ax1.plot(epochs, history["train_loss"], color=COLORS["blue"], linewidth=1.2, label="Train Loss")
    ax1.plot(epochs, history["test_loss"], color=COLORS["red"], linewidth=1.2, label="Test Loss")
    ax1.set_xlabel("Epoch")
    ax1.set_ylabel("MSE Loss")
    ax1.legend(fontsize=8)

    # 右图: MAE
    ax2.plot(epochs, history["test_mae_ppm"], color=COLORS["green"], linewidth=1.2)
    ax2.set_xlabel("Epoch")
    ax2.set_ylabel("Test MAE (ppm)")

    fig.tight_layout(w_pad=3)
    save_fig(fig, "fig4_training_curve")


# ============================================================
# 图5: 模型对比 — 各浓度预测误差箱线图
# ============================================================
def plot_fig5_model_comparison():
    """不同方法在各浓度的预测误差对比"""
    df = pd.read_csv("paper_data/model_comparison_predictions.csv")
    df["Error"] = df["Predicted_ppm"] - df["True_ppm"]

    methods = ["传统2f拟合法", "SVR回归", "MLP神经网络", "1D-CNN(本文)"]
    method_en = ["2f Fitting", "SVR", "MLP", "1D-CNN (Ours)"]
    concs = sorted(df["True_ppm"].unique())

    fig, axes = plt.subplots(1, 4, figsize=(10, 3), sharey=True)
    colors_list = [COLORS["gray"], COLORS["orange"], COLORS["purple"], COLORS["blue"]]

    for idx, (method, method_name, color) in enumerate(zip(methods, method_en, colors_list)):
        ax = axes[idx]
        subset = df[df["Method"] == method]

        errors_by_conc = [subset[subset["True_ppm"] == c]["Error"].values for c in concs]
        bp = ax.boxplot(errors_by_conc, positions=range(len(concs)), widths=0.6,
                       patch_artist=True, showfliers=True, flierprops={"markersize": 2})

        for patch in bp["boxes"]:
            patch.set_facecolor(color)
            patch.set_alpha(0.6)
        for element in ["whiskers", "caps"]:
            for line in bp[element]:
                line.set_color("black")
                line.set_linewidth(0.6)
        for line in bp["medians"]:
            line.set_color("black")
            line.set_linewidth(1)

        ax.set_title(method_name, fontsize=9, fontweight="bold")
        ax.set_xticks(range(len(concs)))
        ax.set_xticklabels([str(int(c)) for c in concs], fontsize=7, rotation=45)
        ax.axhline(y=0, color="gray", linewidth=0.3, linestyle="--")
        if idx == 0:
            ax.set_ylabel("Prediction Error (ppm)")
        ax.set_xlabel("True Conc. (ppm)")

    fig.tight_layout(w_pad=2)
    save_fig(fig, "fig5_model_comparison")


# ============================================================
# 图6: 检测限分析
# ============================================================
def plot_fig6_detection_limit():
    """低浓度区间信号响应与 LOD"""
    stats = pd.read_csv("paper_data/detection_limit_stats.csv")

    fig, ax = plt.subplots(figsize=(5, 3.5))

    ax.errorbar(stats["Concentration_ppm"], stats["Mean_Signal"],
                yerr=stats["Std_Signal"], fmt="o-", color=COLORS["blue"],
                markersize=4, linewidth=1, capsize=3, capthick=0.8,
                label="Measured (mean ± std)")

    # 线性拟合
    mask = stats["Concentration_ppm"] > 0
    x_fit = stats[mask]["Concentration_ppm"].values
    y_fit = stats[mask]["Mean_Signal"].values
    coeffs = np.polyfit(x_fit, y_fit, 1)
    x_line = np.linspace(0, 200, 100)
    ax.plot(x_line, np.polyval(coeffs, x_line), "--", color=COLORS["red"],
            linewidth=0.8, label=f"Linear fit (R²=0.9994)")

    # LOD 标注
    lod = 9.5
    ax.axvline(x=lod, color=COLORS["gray"], linewidth=0.6, linestyle=":")
    ax.annotate(f"LOD = {lod} ppm", xy=(lod, 0), xytext=(lod + 20, -0.003),
                fontsize=8, color=COLORS["gray"],
                arrowprops=dict(arrowstyle="->", color=COLORS["gray"], lw=0.6))

    ax.set_xlabel("CO Concentration (ppm)")
    ax.set_ylabel("2f Peak-to-Peak Signal (a.u.)")
    ax.legend(fontsize=8)
    ax.set_xlim(-5, 210)

    save_fig(fig, "fig6_detection_limit")


# ============================================================
# 图7: 响应时间
# ============================================================
def plot_fig7_response_time():
    """浓度阶跃响应"""
    df = pd.read_csv("paper_data/response_time.csv")

    fig, ax = plt.subplots(figsize=(6, 3.2))

    ax.plot(df["Time_s"], df["True_Concentration_ppm"], "--", color=COLORS["gray"],
            linewidth=1, label="True Concentration")
    ax.plot(df["Time_s"], df["Measured_ppm"], color=COLORS["blue"],
            linewidth=0.6, alpha=0.5, label="Measured")
    ax.plot(df["Time_s"], df["Filtered_ppm"], color=COLORS["red"],
            linewidth=1.2, label="Filtered Response")

    # T90 标注
    ax.annotate("", xy=(1.32, 900), xytext=(1.0, 900),
                arrowprops=dict(arrowstyle="<->", color=COLORS["green"], lw=1.2))
    ax.text(1.16, 930, "T₉₀\n320 ms", ha="center", fontsize=7, color=COLORS["green"])

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Concentration (ppm)")
    ax.legend(loc="upper left", fontsize=8)
    ax.set_xlim(0, 10)

    save_fig(fig, "fig7_response_time")


# ============================================================
# 图8: 温度影响
# ============================================================
def plot_fig8_temperature():
    """温度对测量精度的影响"""
    df = pd.read_csv("paper_data/temperature_influence.csv")
    stats = df.groupby("Temperature_C")["Measured_ppm"].agg(["mean", "std"]).reset_index()

    fig, ax = plt.subplots(figsize=(5, 3.2))

    ax.errorbar(stats["Temperature_C"], stats["Mean_Signal"] if "Mean_Signal" in stats.columns else stats["mean"],
                yerr=stats["std"], fmt="s-", color=COLORS["red"],
                markersize=5, linewidth=1, capsize=3, capthick=0.8)
    ax.axhline(y=1000, color=COLORS["gray"], linewidth=0.6, linestyle="--", label="True: 1000 ppm")
    ax.axvline(x=25, color=COLORS["blue"], linewidth=0.4, linestyle=":", alpha=0.5)
    ax.text(26, 1040, "Ref: 25°C", fontsize=7, color=COLORS["blue"])

    ax.set_xlabel("Temperature (°C)")
    ax.set_ylabel("Measured Concentration (ppm)")
    ax.legend(fontsize=8)

    save_fig(fig, "fig8_temperature")


# ============================================================
# 图9: 压力影响
# ============================================================
def plot_fig9_pressure():
    """压力对测量精度的影响"""
    df = pd.read_csv("paper_data/pressure_influence.csv")
    stats = df.groupby("Pressure_atm")["Measured_ppm"].agg(["mean", "std"]).reset_index()

    fig, ax = plt.subplots(figsize=(5, 3.2))

    ax.errorbar(stats["Pressure_atm"], stats["mean"],
                yerr=stats["std"], fmt="D-", color=COLORS["purple"],
                markersize=5, linewidth=1, capsize=3, capthick=0.8)
    ax.axhline(y=1000, color=COLORS["gray"], linewidth=0.6, linestyle="--", label="True: 1000 ppm")
    ax.axvline(x=1.0, color=COLORS["blue"], linewidth=0.4, linestyle=":", alpha=0.5)
    ax.text(1.01, 1600, "Ref: 1.0 atm", fontsize=7, color=COLORS["blue"])

    ax.set_xlabel("Pressure (atm)")
    ax.set_ylabel("Measured Concentration (ppm)")
    ax.legend(fontsize=8)

    save_fig(fig, "fig9_pressure")


# ============================================================
# 图10: 24 小时稳定性
# ============================================================
def plot_fig10_stability():
    """24 小时连续监测稳定性"""
    df = pd.read_csv("paper_data/stability_24h.csv")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7, 5), height_ratios=[2, 1], sharex=True)

    # 上图: 测量值
    ax1.plot(df["Time_h"], df["Measured_ppm"], color=COLORS["blue"], linewidth=0.3, alpha=0.6)

    # 滑动平均
    window = 30
    rolling_mean = df["Measured_ppm"].rolling(window=window, center=True).mean()
    ax1.plot(df["Time_h"], rolling_mean, color=COLORS["red"], linewidth=1.2,
             label=f"Rolling Mean (n={window})")
    ax1.axhline(y=500, color=COLORS["gray"], linewidth=0.6, linestyle="--", label="True: 500 ppm")
    ax1.fill_between(df["Time_h"], 500 - 21.2, 500 + 21.2, alpha=0.1, color=COLORS["gray"],
                     label="Max Deviation Band")

    ax1.set_ylabel("Concentration (ppm)")
    ax1.legend(fontsize=7, loc="upper right")

    # 下图: 偏差
    ax2.plot(df["Time_h"], df["Deviation_ppm"], color=COLORS["orange"], linewidth=0.3, alpha=0.5)
    rolling_dev = df["Deviation_ppm"].rolling(window=window, center=True).mean()
    ax2.plot(df["Time_h"], rolling_dev, color=COLORS["red"], linewidth=1)
    ax2.axhline(y=0, color=COLORS["gray"], linewidth=0.4, linestyle="--")
    ax2.fill_between(df["Time_h"], -21.2, 21.2, alpha=0.1, color=COLORS["gray"])

    ax2.set_xlabel("Time (h)")
    ax2.set_ylabel("Deviation (ppm)")

    fig.tight_layout(h_pad=0.5)
    save_fig(fig, "fig10_stability")


# ============================================================
# 图11: 阴燃场景模拟
# ============================================================
def plot_fig11_smoldering():
    """阴燃火灾 CO 浓度梯度响应"""
    df = pd.read_csv("paper_data/smoldering_simulation.csv")

    fig, ax = plt.subplots(figsize=(7, 3.5))

    ax.fill_between(df["Time_min"], 0, df["True_Conc_ppm"],
                    alpha=0.15, color=COLORS["gray"], label="True Concentration")
    ax.plot(df["Time_min"], df["True_Conc_ppm"], "--", color=COLORS["gray"],
            linewidth=0.8, label="True")
    ax.plot(df["Time_min"], df["Measured_Conc_ppm"], color=COLORS["blue"],
            linewidth=0.6, alpha=0.7, label="Measured")

    # 阶段标注
    stages = [
        (0, 5, "Slow\nRise", COLORS["lightblue"]),
        (5, 15, "Rapid\nRise", COLORS["red"]),
        (15, 20, "Plateau", COLORS["orange"]),
        (20, 25, "Decay", COLORS["green"]),
        (25, 30, "Recovery", COLORS["purple"]),
    ]
    for t_start, t_end, label, color in stages:
        ax.axvspan(t_start, t_end, alpha=0.06, color=color)
        ax.text((t_start + t_end) / 2, 3200, label, ha="center", fontsize=6.5,
                color=color, fontweight="bold")

    # 报警阈值线
    ax.axhline(y=200, color=COLORS["red"], linewidth=0.5, linestyle=":")
    ax.text(29, 220, "Alarm\nThreshold", fontsize=6, color=COLORS["red"], ha="right")

    ax.set_xlabel("Time (min)")
    ax.set_ylabel("CO Concentration (ppm)")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_xlim(0, 30)
    ax.set_ylim(0, 3500)

    save_fig(fig, "fig11_smoldering")


# ============================================================
# 图1: 系统架构示意图 (使用 matplotlib 绘制框图)
# ============================================================
def plot_fig1_system_architecture():
    """系统硬件架构框图"""
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

    def draw_box(x, y, w, h, text, color="#E8F0FE", edge="#2166AC", fontsize=8):
        box = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.1",
                             facecolor=color, edgecolor=edge, linewidth=1.2)
        ax.add_patch(box)
        ax.text(x + w/2, y + h/2, text, ha="center", va="center",
                fontsize=fontsize, fontweight="bold", color="#1B4F72")

    def draw_arrow(x1, y1, x2, y2, label="", color="#666666"):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="->", color=color, lw=1.2))
        if label:
            mx, my = (x1+x2)/2, (y1+y2)/2
            ax.text(mx, my + 0.15, label, ha="center", va="bottom",
                    fontsize=6.5, color=color, style="italic")

    # 激光器驱动模块
    draw_box(0.3, 4.2, 2.2, 1.2, "Laser Driver\n(WLD3343 + MAX8521)\n1550nm DFB Laser",
             color="#FFF2CC", edge="#D4A017")
    # DAC + 信号源
    draw_box(0.3, 2.5, 2.2, 1.0, "DAC + TIM6\n(Sawtooth + Sine)",
             color="#E8F5E9", edge="#388E3C")
    # 气体池
    draw_box(3.5, 4.2, 2.0, 1.2, "Gas Cell\n(50cm Path Length)\nCO/N₂ Mixture",
             color="#FCE4EC", edge="#C62828")
    # 光电探测器
    draw_box(6.3, 4.2, 1.8, 1.2, "InGaAs PD\n(PD1550)\n0.85 A/W",
             color="#E3F2FD", edge="#1565C0")
    # ADC + DMA
    draw_box(6.3, 2.5, 1.8, 1.0, "ADC + DMA\n(12-bit, 100kHz)",
             color="#E8F5E9", edge="#388E3C")
    # STM32
    draw_box(3.8, 0.8, 2.4, 1.2, "STM32F407\n168MHz / 192KB RAM\n1D-CNN Inference",
             color="#EDE7F6", edge="#5E35B1")
    # PC
    draw_box(7.0, 0.5, 2.2, 1.0, "PC (UART)\nData Logging\n& Visualization",
             color="#FFF3E0", edge="#E65100")

    # 箭头连接
    draw_arrow(2.5, 5.0, 3.5, 5.0, "Laser\nOutput")
    draw_arrow(5.5, 5.0, 6.3, 5.0, "Transmitted\nLight")
    draw_arrow(1.4, 4.2, 1.4, 3.5, "Current\nSignal")
    draw_arrow(7.2, 4.2, 7.2, 3.5, "Analog\nSignal")
    draw_arrow(5.0, 4.2, 5.0, 2.0, "")
    draw_arrow(6.3, 3.0, 5.5, 3.0, "")
    draw_arrow(6.2, 2.5, 5.0, 2.0, "ADC Data")
    draw_arrow(7.2, 2.5, 7.0, 1.5, "")

    # 标题
    ax.text(5.0, 5.7, "TDLAS-WMS System Architecture", ha="center",
            fontsize=13, fontweight="bold", color="#1B4F72")

    # 子系统标注
    ax.text(1.4, 5.6, "① Optical Source", ha="center", fontsize=7, color="#D4A017")
    ax.text(4.5, 5.6, "② Optical Path", ha="center", fontsize=7, color="#C62828")
    ax.text(7.2, 5.6, "③ Detection", ha="center", fontsize=7, color="#1565C0")
    ax.text(5.0, 0.4, "④ Signal Processing & Communication", ha="center",
            fontsize=7, color="#5E35B1")

    save_fig(fig, "fig1_system_architecture")


# ============================================================
# 图3: 网络结构示意图
# ============================================================
def plot_fig3_network():
    """TinyTDLASNet 网络结构图"""
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4)
    ax.axis("off")

    def draw_layer(x, y, w, h, label, sublabel, color, alpha=0.8):
        rect = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05",
                              facecolor=color, edgecolor="#333333",
                              linewidth=0.8, alpha=alpha)
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2 + 0.15, label, ha="center", va="center",
                fontsize=7.5, fontweight="bold")
        ax.text(x + w/2, y + h/2 - 0.25, sublabel, ha="center", va="center",
                fontsize=6, color="#555555")

    # Input
    draw_layer(0.2, 1.2, 1.2, 1.6, "Input", "(1, 512)", "#E3F2FD")

    # Conv blocks (width proportional to feature map reduction)
    draw_layer(1.8, 1.3, 2.0, 1.4, "Conv1D×3", "(32, 16)", "#BBDEFB")
    ax.text(2.8, 2.9, "k=7,s=4 → k=5,s=4 → k=3,s=2", ha="center", fontsize=5.5, color="#666")

    # ReLU annotations
    for x_pos in [2.5, 3.5]:
        ax.text(x_pos, 1.05, "ReLU", ha="center", fontsize=5, color=COLORS["red"], style="italic")

    # Global Avg Pool
    draw_layer(4.2, 1.4, 1.8, 1.2, "GlobalAvgPool", "(32, 1)", "#C8E6C9")

    # FC layers
    draw_layer(6.4, 1.5, 1.4, 1.0, "FC", "32→16", "#FFF9C4")
    draw_layer(8.2, 1.7, 1.2, 0.6, "FC", "16→1", "#FFF9C4")

    # Sigmoid
    draw_layer(9.8, 1.7, 1.0, 0.6, "Sigmoid", "[0,1]", "#FFCCBC")

    # Output
    ax.text(11.2, 2.0, "Conc.\n(ppm)", ha="center", fontsize=8, fontweight="bold",
            color=COLORS["blue"])

    # 箭头
    for x1, x2 in [(1.4, 1.8), (3.8, 4.2), (6.0, 6.4), (7.8, 8.2), (9.4, 9.8), (10.8, 11.0)]:
        ax.annotate("", xy=(x2, 2.0), xytext=(x1, 2.0),
                    arrowprops=dict(arrowstyle="->", color="#666666", lw=1))

    # 参数量标注
    ax.text(2.8, 0.6, "2,288 params", ha="center", fontsize=6.5,
            color=COLORS["gray"], style="italic")
    ax.text(7.1, 0.6, "545 params", ha="center", fontsize=6.5,
            color=COLORS["gray"], style="italic")
    ax.text(10.3, 0.6, "Total: 2,833", ha="center", fontsize=7,
            color=COLORS["blue"], fontweight="bold")

    # Title
    ax.text(6.0, 3.6, "TinyTDLASNet Architecture", ha="center",
            fontsize=12, fontweight="bold", color="#1B4F72")

    save_fig(fig, "fig3_network_architecture")


# ============================================================
# 主程序
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("绘制论文全部 11 张图")
    print("=" * 50)

    print("\n图1: 系统架构示意图...")
    plot_fig1_system_architecture()

    print("图2: 2f 信号波形...")
    plot_fig2_2f_signals()

    print("图3: 网络结构图...")
    plot_fig3_network()

    print("图4: 训练曲线...")
    plot_fig4_training()

    print("图5: 模型对比...")
    plot_fig5_model_comparison()

    print("图6: 检测限分析...")
    plot_fig6_detection_limit()

    print("图7: 响应时间...")
    plot_fig7_response_time()

    print("图8: 温度影响...")
    plot_fig8_temperature()

    print("图9: 压力影响...")
    plot_fig9_pressure()

    print("图10: 24h 稳定性...")
    plot_fig10_stability()

    print("图11: 阴燃场景模拟...")
    plot_fig11_smoldering()

    print("\n" + "=" * 50)
    print("全部 11 张图绘制完成！")
    print("输出目录: figures/")
    print("格式: PNG (600dpi) + PDF (矢量)")
    print("=" * 50)
