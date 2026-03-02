-- ============================================================
-- StockBatch Supabase Schema
-- 執行方式：Supabase Dashboard > SQL Editor > 貼入執行
-- ============================================================

-- 股票宇宙（0050 成分股）
CREATE TABLE IF NOT EXISTS stock_universe (
    stock_id    VARCHAR(10) PRIMARY KEY,
    stock_name  VARCHAR(100),
    percentage  NUMERIC(6,4),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 每日收盤價 + 均線
CREATE TABLE IF NOT EXISTS daily_price (
    stock_id  VARCHAR(10)  NOT NULL,
    date      DATE         NOT NULL,
    open      NUMERIC(10,2),
    high      NUMERIC(10,2),
    low       NUMERIC(10,2),
    close     NUMERIC(10,2),
    volume    BIGINT,
    ma5       NUMERIC(10,2),
    ma20      NUMERIC(10,2),
    ma60      NUMERIC(10,2),
    PRIMARY KEY (stock_id, date)
);

-- 每日三大法人
CREATE TABLE IF NOT EXISTS daily_institutional (
    stock_id        VARCHAR(10) NOT NULL,
    date            DATE        NOT NULL,
    foreign_net     BIGINT,
    trust_net       BIGINT,
    dealer_net      BIGINT,
    total_net       BIGINT,
    foreign_streak  INT,   -- 正=連買天數, 負=連賣天數
    trust_streak    INT,
    PRIMARY KEY (stock_id, date)
);

-- 每日融資融券
CREATE TABLE IF NOT EXISTS daily_margin (
    stock_id        VARCHAR(10) NOT NULL,
    date            DATE        NOT NULL,
    margin_balance  BIGINT,
    short_balance   BIGINT,
    margin_chg_pct  NUMERIC(8,4),  -- 與 20 日前比較的變化率
    PRIMARY KEY (stock_id, date)
);

-- 月營收
CREATE TABLE IF NOT EXISTS monthly_revenue (
    stock_id     VARCHAR(10) NOT NULL,
    year         INT         NOT NULL,
    month        INT         NOT NULL,
    revenue      BIGINT,
    revenue_mom  NUMERIC(8,2),
    revenue_yoy  NUMERIC(8,2),
    PRIMARY KEY (stock_id, year, month)
);

-- 季度損益（EPS、三率）
CREATE TABLE IF NOT EXISTS quarterly_income (
    stock_id         VARCHAR(10) NOT NULL,
    year             INT         NOT NULL,
    quarter          INT         NOT NULL,
    eps              NUMERIC(8,2),
    gross_margin     NUMERIC(8,2),
    operating_margin NUMERIC(8,2),
    net_margin       NUMERIC(8,2),
    eps_qoq          NUMERIC(8,2),
    PRIMARY KEY (stock_id, year, quarter)
);

-- 季度資產負債表
CREATE TABLE IF NOT EXISTS quarterly_balance (
    stock_id      VARCHAR(10) NOT NULL,
    year          INT         NOT NULL,
    quarter       INT         NOT NULL,
    debt_ratio    NUMERIC(8,2),
    current_ratio NUMERIC(8,2),
    quick_ratio   NUMERIC(8,2),
    PRIMARY KEY (stock_id, year, quarter)
);

-- 季度現金流量
CREATE TABLE IF NOT EXISTS quarterly_cashflow (
    stock_id    VARCHAR(10) NOT NULL,
    year        INT         NOT NULL,
    quarter     INT         NOT NULL,
    operating_cf BIGINT,
    net_income   BIGINT,
    ocf_quality  NUMERIC(8,4),  -- OCF / 淨利
    PRIMARY KEY (stock_id, year, quarter)
);

-- 週度股權分散（大戶持股比）
CREATE TABLE IF NOT EXISTS weekly_shareholding (
    stock_id       VARCHAR(10) NOT NULL,
    date           DATE        NOT NULL,
    big_holder_pct NUMERIC(8,4),
    PRIMARY KEY (stock_id, date)
);

-- 本益比 / 股淨比
CREATE TABLE IF NOT EXISTS valuation (
    stock_id VARCHAR(10) NOT NULL,
    date     DATE        NOT NULL,
    per      NUMERIC(8,2),
    pbr      NUMERIC(8,2),
    PRIMARY KEY (stock_id, date)
);

-- 每週評分紀錄
CREATE TABLE IF NOT EXISTS weekly_scores (
    stock_id             VARCHAR(10)  NOT NULL,
    week_date            DATE         NOT NULL,
    profitability_score  NUMERIC(5,1),
    health_score         NUMERIC(5,1),
    chip_score           NUMERIC(5,1),
    momentum_score       NUMERIC(5,1),
    total_score          NUMERIC(5,1),
    passes_filter        BOOLEAN,
    filter_reason        TEXT,
    PRIMARY KEY (stock_id, week_date)
);
