/**
 * STM32 阿波罗开发板 — TDLAS 系统底层外设驱动
 * ================================================
 * 功能:
 *   1. DAC 定时输出高频正弦与低频锯齿波叠加信号（驱动激光器）
 *   2. ADC 配合 DMA 高速连续采样（读取光电探测器信号）
 *   3. UART 串口配置，将 ADC 原始数据实时发给 PC
 *
 * 开发环境: STM32F407 阿波罗开发板, Keil MDK, 裸机开发（HAL 库）
 *
 * 硬件连接:
 *   - DAC Channel 1 (PA4) -> 激光器恒流驱动输入
 *   - ADC Channel 0  (PA0) -> 光电探测器 TIA 输出
 *   - USART1 TX (PA9) / RX (PA10) -> USB转串口 -> PC
 *
 * 生成时间: Tue May 19 01:27:41 2026
 */

#include "stm32f4xx_hal.h"
#include <math.h>
#include <string.h>

/* ============================================================
 * 1. 全局变量与宏定义
 * ============================================================ */

/* --- DAC 相关参数 --- */
#define DAC_BUFFER_SIZE      256     /* DAC DMA 缓冲区大小（一个完整波形周期的采样点数） */
#define SINE_POINTS_PER_CYCLE 64    /* 每个正弦周期的采样点数 */
#define SAWTOOTH_CYCLES      4      /* 一个锯齿波周期内包含的正弦周期数 */
#define DAC_RESOLUTION       4096   /* 12-bit DAC 分辨率: 2^12 = 4096 */
#define DAC_VREF             3.3f   /* DAC 参考电压 (V) */

/* DAC DMA 双缓冲区（一个正在输出时另一个可以准备新数据） */
uint16_t dac_buffer[DAC_BUFFER_SIZE];
uint16_t dac_buffer_alt[DAC_BUFFER_SIZE];

/* --- ADC 相关参数 --- */
#define ADC_BUFFER_SIZE      1024   /* ADC DMA 缓冲区大小 */
#define ADC_RESOLUTION       4096   /* 12-bit ADC 分辨率 */
#define ADC_VREF             3.3f   /* ADC 参考电压 (V) */

/* ADC DMA 双缓冲区 */
uint16_t adc_buffer[ADC_BUFFER_SIZE];
uint16_t adc_buffer_alt[ADC_BUFFER_SIZE];

/* --- UART 相关参数 --- */
#define UART_BAUDRATE        115200 /* 串口波特率 */
#define UART_TX_BUFFER_SIZE  2048   /* 串口发送缓冲区 */

uint8_t uart_tx_buffer[UART_TX_BUFFER_SIZE];

/* --- 句柄变量 --- */
DAC_HandleTypeDef  hdac;
TIM_HandleTypeDef  htim_dac;    /* DAC 触发定时器 */
TIM_HandleTypeDef  htim_adc;    /* ADC 触发定时器 */
DMA_HandleTypeDef  hdma_dac;
DMA_HandleTypeDef  hdma_adc;
ADC_HandleTypeDef  hadc;
UART_HandleTypeDef huart1;

/* --- 系统状态标志 --- */
volatile uint8_t dac_half_transfer = 0;   /* DAC DMA 半传输完成标志 */
volatile uint8_t adc_half_transfer = 0;   /* ADC DMA 半传输完成标志 */
volatile uint32_t adc_sample_count = 0;   /* ADC 累计采样计数 */

/* ============================================================
 * 2. DAC 驱动 — 生成正弦+锯齿波叠加信号
 * ============================================================ */

/**
 * @brief  生成正弦波与锯齿波叠加的 DAC 波形数据
 *
 * 工作原理:
 *   激光器需要的注入电流波形 = 低频锯齿波（用于波长扫描）+ 高频正弦波（用于 WMS 调制）
 *   DAC 输出电压经 V/I 转换电路后驱动激光器
 *
 *   锯齿波频率: 约 100Hz（一个周期扫描过整个吸收线）
 *   正弦波频率: 约 10kHz（WMS 调制频率）
 *   关系: 每个锯齿波周期内有 ~100 个正弦周期
 *
 * @param  buffer:     目标缓冲区指针
 * @param  size:       缓冲区大小
 * @param  sine_amp:   正弦波幅度（DAC 码值，0-2047）
 * @param  sawtooth_amp: 锯齿波幅度（DAC 码值，0-2047）
 * @param  dc_offset:  直流偏置（DAC 码值，0-4095）
 */
void DAC_GenerateWaveform(uint16_t *buffer, uint16_t size,
                          uint16_t sine_amp, uint16_t sawtooth_amp, uint16_t dc_offset)
{
    for (uint16_t i = 0; i < size; i++)
    {
        /* --- 低频锯齿波分量 --- */
        /* 锯齿波: 在 0 到 2*PI 范围内线性递增，产生三角形扫描波 */
        /* 使用三角波代替纯锯齿波（上升+下降），避免回扫突变 */
        float phase_saw = (float)i / (float)size;  /* [0, 1) */
        float sawtooth;
        if (phase_saw < 0.5f)
            sawtooth = 2.0f * phase_saw;           /* 上升段 [0, 1] */
        else
            sawtooth = 2.0f * (1.0f - phase_saw);  /* 下降段 [1, 0] */

        /* --- 高频正弦波分量 --- */
        /* 正弦波: 每个缓冲区周期内完成 SAWTOOTH_CYCLES 个完整正弦振荡 */
        float phase_sine = (float)i * SAWTOOTH_CYCLES / (float)size;
        float sine_val = sinf(2.0f * 3.14159265f * phase_sine);

        /* --- 叠加 --- */
        /* 将浮点值 [-1,1] 转换为 12-bit DAC 码值 */
        float combined = (float)dc_offset
                       + sine_amp * sine_val
                       + sawtooth_amp * (sawtooth - 0.5f);  /* 锯齿波居中到 [-0.5, 0.5] */

        /* 钳位到 DAC 有效范围 [0, 4095] */
        if (combined < 0.0f) combined = 0.0f;
        if (combined > (float)(DAC_RESOLUTION - 1)) combined = (float)(DAC_RESOLUTION - 1);

        buffer[i] = (uint16_t)combined;
    }
}

/**
 * @brief  初始化 DAC 外设
 *
 * 配置步骤:
 *   1. 使能 DAC 时钟
 *   2. 配置 DAC Channel 1 输出 (PA4 引脚)
 *   3. 配置 DMA 使能（DAC 数据由 DMA 自动搬运）
 *   4. 配置触发源为 TIM6 TRGO
 */
void DAC_Init(void)
{
    /* 使能 GPIOA 和 DAC 时钟 */
    __HAL_RCC_DAC_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();

    /* 配置 PA4 为模拟模式（DAC 输出引脚） */
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Pin  = GPIO_PIN_4;
    GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    /* DAC 通道初始化 */
    DAC_ChannelConfTypeDef sConfig = {0};
    hdac.Instance = DAC;
    HAL_DAC_Init(&hdac);

    /* 配置 DAC Channel 1:
     *   - 触发源: TIM6 TRGO（定时器更新事件触发 DAC 转换）
     *   - 输出缓冲: 使能（降低输出阻抗，但会略微增加功耗）
     *   - 波形生成: 关闭（我们用 DMA 搬运自定义波形，不需要三角波/噪声发生器） */
    sConfig.DAC_Trigger      = DAC_TRIGGER_T6_TRGO;
    sConfig.DAC_OutputBuffer = DAC_OUTPUTBUFFER_ENABLE;
    HAL_DAC_ConfigChannel(&hdac, &sConfig, DAC_CHANNEL_1);
}

/**
 * @brief  初始化 DAC 触发定时器 TIM6
 *
 * TIM6 的作用是周期性触发 DAC 转换，控制波形输出速率。
 *
 * 计算公式:
 *   DAC 输出频率 = TIM6 时钟 / (PSC+1) / (ARR+1)
 *   期望: ~100kHz（每个锯齿波周期 256 点，对应锯齿波频率 ~400Hz）
 *
 * STM32F407 TIM6 时钟: 84MHz (APB1 Timer Clock)
 *   ARR = 84MHz / 100kHz - 1 = 839
 */
void DAC_TIM6_Init(void)
{
    __HAL_RCC_TIM6_CLK_ENABLE();

    htim_dac.Instance               = TIM6;
    htim_dac.Init.Prescaler         = 0;       /* 不分频，84MHz 直接计数 */
    htim_dac.Init.CounterMode       = TIM_COUNTERMODE_UP;
    htim_dac.Init.Period            = 839;     /* 84MHz / (839+1) = 100kHz 触发频率 */
    htim_dac.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
    HAL_TIM_Base_Init(&htim_dac);

    /* 配置 TIM6 主模式输出 TRGO 信号（更新事件触发） */
    TIM_MasterConfigTypeDef sMasterConfig = {0};
    sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
    HAL_TIMEx_MasterConfigSynchronization(&htim_dac, &sMasterConfig);
}

/**
 * @brief  初始化 DAC DMA
 *
 * DMA 的作用: 自动将 dac_buffer 中的波形数据搬运到 DAC 数据寄存器，
 * 无需 CPU 介入，释放 CPU 资源给其他任务（如信号处理）。
 *
 * DMA 配置:
 *   - 模式: 循环模式（永不停止，反复输出波形）
 *   - 方向: 内存 -> 外设
 *   - 数据宽度: 半字 (16-bit，匹配 DAC 12-bit 数据寄存器)
 */
void DAC_DMA_Init(void)
{
    __HAL_RCC_DMA1_CLK_ENABLE();

    /* DMA1 Stream5 Channel7 对应 DAC Channel 1 */
    hdma_dac.Instance                 = DMA1_Stream5;
    hdma_dac.Init.Channel             = DMA_CHANNEL_7;
    hdma_dac.Init.Direction           = DMA_MEMORY_TO_PERIPH;
    hdma_dac.Init.PeriphInc           = DMA_PINC_DISABLE;    /* 外设地址固定 (DAC 数据寄存器) */
    hdma_dac.Init.MemInc              = DMA_MINC_ENABLE;     /* 内存地址自增 */
    hdma_dac.Init.PeriphDataAlignment = DMA_PDATAALIGN_HALFWORD; /* 外设半字对齐 */
    hdma_dac.Init.MemDataAlignment    = DMA_MDATAALIGN_HALFWORD;
    hdma_dac.Init.Mode                = DMA_CIRCULAR;        /* 循环模式 */
    hdma_dac.Init.Priority            = DMA_PRIORITY_HIGH;
    hdma_dac.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
    HAL_DMA_Init(&hdma_dac);

    /* 将 DMA 关联到 DAC 句柄 */
    __HAL_LINKDMA(&hdac, DMA_Handle1, hdma_dac);

    /* 配置 NVIC 中断（DMA 半传输和传输完成中断） */
    HAL_NVIC_SetPriority(DMA1_Stream5_IRQn, 1, 0);
    HAL_NVIC_EnableIRQ(DMA1_Stream5_IRQn);
}

/**
 * @brief  启动 DAC DMA 波形输出
 */
void DAC_Start(void)
{
    /* 生成初始波形数据到两个缓冲区 */
    DAC_GenerateWaveform(dac_buffer,     DAC_BUFFER_SIZE, 512, 1024, 2048);
    DAC_GenerateWaveform(dac_buffer_alt, DAC_BUFFER_SIZE, 512, 1024, 2048);

    /* 启动 DMA 传输（循环模式） */
    HAL_DAC_Start_DMA(&hdac, DAC_CHANNEL_1,
                      (uint32_t *)dac_buffer, DAC_BUFFER_SIZE, DAC_ALIGN_12B_R);

    /* 启动 TIM6，开始触发 DAC 转换 */
    HAL_TIM_Base_Start(&htim_dac);
}

/* ============================================================
 * 3. ADC 驱动 — DMA 高速连续采样
 * ============================================================ */

/**
 * @brief  初始化 ADC 外设
 *
 * 配置 ADC1 Channel 0 (PA0) 进行高速连续采样。
 * 使用 DMA 自动搬运采样数据，CPU 完全不参与单次转换。
 *
 * ADC 配置要点:
 *   - 分辨率: 12-bit (0-4095 对应 0-3.3V)
 *   - 采样时间: 3 个周期（尽量快，配合 DMA 实现高速采样）
 *   - 扫描模式: 关闭（单通道采样）
 *   - 连续转换: 使能（采完一个自动开始下一个）
 *   - DMA: 使能（数据自动搬到内存缓冲区）
 */
void ADC_Init(void)
{
    __HAL_RCC_ADC1_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();

    /* 配置 PA0 为模拟输入模式 */
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Pin  = GPIO_PIN_0;
    GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    /* ADC 公共配置（STM32F4 的 ADC 有公共寄存器） */
    ADC_HandleTypeDef *phadc = &hadc;
    phadc->Instance                   = ADC1;
    phadc->Init.Resolution            = ADC_RESOLUTION_12B;   /* 12-bit 分辨率 */
    phadc->Init.ScanConvMode          = DISABLE;              /* 单通道，不需要扫描 */
    phadc->Init.ContinuousConvMode    = ENABLE;               /* 连续转换模式 */
    phadc->Init.DiscontinuousConvMode = DISABLE;
    phadc->Init.ExternalTrigConv      = ADC_EXTERNALTRIGCONV_T2_TRGO; /* TIM2 触发 */
    phadc->Init.ExternalTrigConvEdge  = ADC_EXTERNALTRIGCONVEDGE_RISING;
    phadc->Init.DataAlign             = ADC_DATAALIGN_RIGHT;  /* 数据右对齐 */
    phadc->Init.NbrOfConversion       = 1;                    /* 1 个转换通道 */
    phadc->Init.DMAContinuousRequests  = ENABLE;               /* DMA 连续请求 */
    HAL_ADC_Init(phadc);

    /* 配置 ADC 通道 0 */
    ADC_ChannelConfTypeDef sConfig = {0};
    sConfig.Channel      = ADC_CHANNEL_0;           /* PA0 对应 ADC Channel 0 */
    sConfig.Rank         = 1;                        /* 转换顺序第 1 */
    sConfig.SamplingTime = ADC_SAMPLETIME_3CYCLES;   /* 采样时间 3 个 ADC 时钟周期 */
    HAL_ADC_ConfigChannel(phadc, &sConfig);
}

/**
 * @brief  初始化 ADC 触发定时器 TIM2
 *
 * TIM2 控制 ADC 的采样速率。
 * 采样频率 = TIM2 时钟 / (PSC+1) / (ARR+1)
 *
 * 期望采样率: 100kHz（与 DAC 输出同步）
 * TIM2 时钟: 84MHz
 *   ARR = 84MHz / 100kHz - 1 = 839
 */
void ADC_TIM2_Init(void)
{
    __HAL_RCC_TIM2_CLK_ENABLE();

    htim_adc.Instance               = TIM2;
    htim_adc.Init.Prescaler         = 0;
    htim_adc.Init.CounterMode       = TIM_COUNTERMODE_UP;
    htim_adc.Init.Period            = 839;       /* 100kHz 触发频率 */
    htim_adc.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
    HAL_TIM_Base_Init(&htim_adc);

    /* TIM2 主模式: 更新事件 -> TRGO -> 触发 ADC */
    TIM_MasterConfigTypeDef sMasterConfig = {0};
    sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
    HAL_TIMEx_MasterConfigSynchronization(&htim_adc, &sMasterConfig);
}

/**
 * @brief  初始化 ADC DMA
 *
 * DMA1 Stream0 Channel0 对应 ADC1。
 * 循环模式: DMA 搬满一个缓冲区后自动从头开始，覆盖旧数据。
 */
void ADC_DMA_Init(void)
{
    __HAL_RCC_DMA2_CLK_ENABLE();

    /* DMA2 Stream0 Channel0 对应 ADC1 */
    hdma_adc.Instance                 = DMA2_Stream0;
    hdma_adc.Init.Channel             = DMA_CHANNEL_0;
    hdma_adc.Init.Direction           = DMA_PERIPH_TO_MEMORY;  /* 外设 -> 内存 */
    hdma_adc.Init.PeriphInc           = DMA_PINC_DISABLE;
    hdma_adc.Init.MemInc              = DMA_MINC_ENABLE;
    hdma_adc.Init.PeriphDataAlignment = DMA_PDATAALIGN_HALFWORD;
    hdma_adc.Init.MemDataAlignment    = DMA_MDATAALIGN_HALFWORD;
    hdma_adc.Init.Mode                = DMA_CIRCULAR;          /* 循环模式 */
    hdma_adc.Init.Priority            = DMA_PRIORITY_HIGH;
    hdma_adc.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
    HAL_DMA_Init(&hdma_adc);

    __HAL_LINKDMA(&hadc, DMA_Handle, hdma_adc);

    /* DMA 中断配置 */
    HAL_NVIC_SetPriority(DMA2_Stream0_IRQn, 2, 0);
    HAL_NVIC_EnableIRQ(DMA2_Stream0_IRQn);
}

/**
 * @brief  启动 ADC DMA 连续采样
 */
void ADC_Start(void)
{
    HAL_ADC_Start_DMA(&hadc, (uint32_t *)adc_buffer, ADC_BUFFER_SIZE);
    HAL_TIM_Base_Start(&htim_adc);
}

/* ============================================================
 * 4. UART 驱动 — 串口数据发送
 * ============================================================ */

/**
 * @brief  初始化 USART1
 *
 * 配置:
 *   - 波特率: 115200 bps
 *   - 数据位: 8
 *   - 停止位: 1
 *   - 校验位: 无
 *   - 引脚:   PA9 (TX), PA10 (RX)
 *   - 模式:   收发均使能
 */
void UART_Init(void)
{
    __HAL_RCC_USART1_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();

    /* 配置 PA9 (TX) 和 PA10 (RX) 为复用功能 */
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Pin       = GPIO_PIN_9 | GPIO_PIN_10;
    GPIO_InitStruct.Mode      = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull      = GPIO_PULLUP;
    GPIO_InitStruct.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF7_USART1;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    /* USART1 参数配置 */
    huart1.Instance          = USART1;
    huart1.Init.BaudRate     = UART_BAUDRATE;
    huart1.Init.WordLength   = UART_WORDLENGTH_8B;
    huart1.Init.StopBits     = UART_STOPBITS_1;
    huart1.Init.Parity       = UART_PARITY_NONE;
    huart1.Init.Mode         = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&huart1);
}

/**
 * @brief  通过 UART 发送 ADC 数据帧
 *
 * 数据帧格式（二进制帧，便于 PC 端高效解析）:
 *   [帧头 0xAA 0x55] [数据长度 2B] [ADC 数据 N*2B] [帧尾 0x0D 0x0A]
 *
 * @param  data:  ADC 数据指针
 * @param  len:   数据长度（采样点数）
 */
void UART_SendADCFrame(uint16_t *data, uint16_t len)
{
    uint16_t pos = 0;

    /* 帧头: 2 字节魔术数 */
    uart_tx_buffer[pos++] = 0xAA;
    uart_tx_buffer[pos++] = 0x55;

    /* 数据长度: 2 字节小端序 */
    uart_tx_buffer[pos++] = (uint8_t)(len & 0xFF);
    uart_tx_buffer[pos++] = (uint8_t)((len >> 8) & 0xFF);

    /* ADC 数据: 每个采样点 2 字节，小端序 */
    for (uint16_t i = 0; i < len; i++)
    {
        uart_tx_buffer[pos++] = (uint8_t)(data[i] & 0xFF);
        uart_tx_buffer[pos++] = (uint8_t)((data[i] >> 8) & 0xFF);
    }

    /* 帧尾: 回车换行 */
    uart_tx_buffer[pos++] = 0x0D;
    uart_tx_buffer[pos++] = 0x0A;

    /* 阻塞发送（数据量不大时可接受，高吞吐场景建议改用 DMA 发送） */
    HAL_UART_Transmit(&huart1, uart_tx_buffer, pos, 100);
}

/**
 * @brief  发送 ASCII 格式的 ADC 数据（便于串口助手直接查看）
 *
 * 每行一个采样值，格式: "ADC: 2048\r\n"
 *
 * @param  data: ADC 数据指针
 * @param  len:  数据长度
 */
void UART_SendADC_ASCII(uint16_t *data, uint16_t len)
{
    char line_buf[32];
    for (uint16_t i = 0; i < len; i++)
    {
        int n = snprintf(line_buf, sizeof(line_buf), "ADC: %u\r\n", data[i]);
        HAL_UART_Transmit(&huart1, (uint8_t *)line_buf, n, 10);
    }
}

/* ============================================================
 * 5. DMA 中断回调
 * ============================================================ */

/**
 * @brief  DMA1 Stream5 中断处理（DAC DMA）
 */
void DMA1_Stream5_IRQHandler(void)
{
    HAL_DMA_IRQHandler(&hdma_dac);
}

/**
 * @brief  DMA2 Stream0 中断处理（ADC DMA）
 */
void DMA2_Stream0_IRQHandler(void)
{
    HAL_DMA_IRQHandler(&hdma_adc);
}

/**
 * @brief  DAC DMA 半传输完成回调
 *
 * 当 DMA 搬完前半部分数据时触发，此时可以安全修改前半部分缓冲区，
 * 而后半部分正在输出不会被打断。
 */
void HAL_DAC_ConvHalfCpltCallback(DAC_HandleTypeDef *hdac_handle)
{
    dac_half_transfer = 1;
    /* 可在此更新 dac_buffer 前半部分的波形数据 */
}

/**
 * @brief  DAC DMA 全传输完成回调
 */
void HAL_DAC_ConvCpltCallback(DAC_HandleTypeDef *hdac_handle)
{
    /* 可在此更新 dac_buffer 后半部分的波形数据 */
}

/**
 * @brief  ADC DMA 半传输完成回调
 *
 * 前半部分 adc_buffer[] 填满，可以安全读取并发送。
 */
void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef *hadc_handle)
{
    adc_half_transfer = 1;
}

/**
 * @brief  ADC DMA 全传输完成回调
 *
 * 后半部分 adc_buffer[] 填满。
 */
void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc_handle)
{
    adc_sample_count += ADC_BUFFER_SIZE;
}

/* ============================================================
 * 6. 系统时钟配置 (简化版)
 * ============================================================ */

/**
 * @brief  配置系统时钟为 168MHz
 *
 * HSE(8MHz) -> PLL(x21, /2) -> SYSCLK(168MHz)
 * AHB = 168MHz, APB1 = 42MHz, APB2 = 84MHz
 */
void SystemClock_Config(void)
{
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

    /* 使能电源时钟，配置电压调节器 */
    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

    /* 配置 HSE 和 PLL */
    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    RCC_OscInitStruct.HSEState       = RCC_HSE_ON;
    RCC_OscInitStruct.PLL.PLLState   = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource  = RCC_PLLSOURCE_HSE;
    RCC_OscInitStruct.PLL.PLLM       = 8;    /* HSE / 8 = 1MHz */
    RCC_OscInitStruct.PLL.PLLN       = 336;  /* 1MHz * 336 = 336MHz */
    RCC_OscInitStruct.PLL.PLLP       = RCC_PLLP_DIV2;  /* 336 / 2 = 168MHz */
    RCC_OscInitStruct.PLL.PLLQ       = 7;
    HAL_RCC_OscConfig(&RCC_OscInitStruct);

    /* 配置总线时钟分频 */
    RCC_ClkInitStruct.ClockType      = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK
                                     | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    RCC_ClkInitStruct.SYSCLKSource   = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.AHBCLKDivider  = RCC_SYSCLK_DIV1;    /* AHB = 168MHz */
    RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;      /* APB1 = 42MHz (TIM6, TIM2 时钟 x2 = 84MHz) */
    RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;      /* APB2 = 84MHz */
    HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5);
}

/* ============================================================
 * 7. 主函数
 * ============================================================ */

int main(void)
{
    /* ---- 系统初始化 ---- */
    HAL_Init();
    SystemClock_Config();

    /* ---- 外设初始化 ---- */
    DAC_Init();          /* DAC 通道初始化 */
    DAC_TIM6_Init();     /* DAC 触发定时器 */
    DAC_DMA_Init();      /* DAC DMA 初始化 */

    ADC_Init();          /* ADC 通道初始化 */
    ADC_TIM2_Init();     /* ADC 触发定时器 */
    ADC_DMA_Init();      /* ADC DMA 初始化 */

    UART_Init();         /* 串口初始化 */

    /* ---- 启动外设 ---- */
    DAC_Start();         /* 启动 DAC 波形输出 */
    ADC_Start();         /* 启动 ADC 连续采样 */

    /* 通过串口发送启动消息 */
    char *welcome = "TDLAS System Started\r\n";
    HAL_UART_Transmit(&huart1, (uint8_t *)welcome, strlen(welcome), 100);

    /* ---- 主循环 ---- */
    while (1)
    {
        /* 检查 ADC DMA 半传输标志 */
        if (adc_half_transfer)
        {
            adc_half_transfer = 0;

            /* 发送前半部分 ADC 数据到 PC */
            UART_SendADCFrame(adc_buffer, ADC_BUFFER_SIZE / 2);
        }

        /* 全传输完成时发送后半部分 */
        /* 注意: 在循环模式下，全传输回调中可以直接处理，
         * 此处放在主循环中轮询是为了避免在中断中做耗时操作 */
        /* 实际工程中可使用双缓冲乒乓操作进一步优化 */

        /* 可在此添加其他任务:
         *   - 简单的 LED 状态指示
         *   - 按键扫描（调整参数）
         *   - 看门狗喂狗 */
    }
}
