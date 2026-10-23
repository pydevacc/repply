import streamlit as st
import numpy as np
import pandas as pd
import plotly.express as px

# ============================================================
# НАСТРОЙКИ ИГРЫ
# ============================================================
SEASON_WEEKS = 26
BASE_DEMAND = 120
DEMAND_NOISE = 0.25
SELL_PRICE = 2500
HOLDING_COST_RATE = 0.02
INITIAL_CASH = 3_000_000
INITIAL_STOCK = 800

SEASON_FACTOR = np.array([
    0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5,
    1.6, 1.7, 1.8, 1.7, 1.5, 1.3, 1.1, 0.9, 0.8, 0.7,
    0.6, 0.5, 0.4, 0.4, 0.3, 0.3
])

SUPPLIERS = {
    "Китайская фабрика": {
        "lead_time": 8, "moq": 500, "price": 800,
        "defect_rate": 0.01, "reliability": 0.95, "delay_weeks": 2,
    },
    "Трейдер": {
        "lead_time": 4, "moq": 100, "price": 950,
        "defect_rate": 0.04, "reliability": 0.85, "delay_weeks": 1,
    },
    "Узбекистан": {
        "lead_time": 5, "moq": 300, "price": 880,
        "defect_rate": 0.02, "reliability": 0.92, "delay_weeks": 1,
    },
}

# ============================================================
# ПЛАН ПРОДАЖ ОТ КОММЕРЦИИ
# ============================================================
def build_commercial_plan():
    plan = []
    for w in range(SEASON_WEEKS):
        season = SEASON_FACTOR[min(w, len(SEASON_FACTOR) - 1)]
        plan.append(int(BASE_DEMAND * season))
    return plan

COMMERCIAL_PLAN = build_commercial_plan()

# ============================================================
# СОСТОЯНИЕ
# ============================================================
def init_state():
    st.session_state.week = 0
    st.session_state.stock = INITIAL_STOCK
    st.session_state.cash = INITIAL_CASH
    st.session_state.pipeline = []
    st.session_state.history = []
    st.session_state.lost_sales = 0
    st.session_state.total_sold = 0
    st.session_state.plan_total = sum(COMMERCIAL_PLAN)
    st.session_state.plan_fulfilled = 0
    st.session_state.last_week_summary = None

if "week" not in st.session_state:
    init_state()

# ============================================================
# МОДЕЛЬ
# ============================================================
def get_demand(week, rng=None):
    season = SEASON_FACTOR[min(week, len(SEASON_FACTOR) - 1)]
    base = BASE_DEMAND * season
    if rng is None:
        noise = np.random.normal(0, DEMAND_NOISE * base)
    else:
        noise = rng.normal(0, DEMAND_NOISE * base)
    return max(0, int(base + noise))

def calc_coverage(week, stock, pipeline):
    arrivals_by_week = {}
    for o in pipeline:
        w = o["arrival_week"]
        good_qty = int(o["qty"] * (1 - o["defect_rate"]))
        arrivals_by_week[w] = arrivals_by_week.get(w, 0) + good_qty

    stock_only_weeks = 0
    sim_stock = stock
    for w in range(week, SEASON_WEEKS):
        plan_w = COMMERCIAL_PLAN[w]
        if sim_stock >= plan_w:
            stock_only_weeks += 1
            sim_stock -= plan_w
        else:
            break

    stock_plus_weeks = 0
    sim_stock = stock
    for w in range(week, SEASON_WEEKS):
        sim_stock += arrivals_by_week.get(w, 0)
        plan_w = COMMERCIAL_PLAN[w]
        if sim_stock >= plan_w:
            stock_plus_weeks += 1
            sim_stock -= plan_w
        else:
            break

    total_plan_ahead = sum(COMMERCIAL_PLAN[week:])
    total_available = stock + sum(arrivals_by_week.values())
    gap = max(0, total_plan_ahead - total_available)

    return {
        "stock_only_weeks": stock_only_weeks,
        "stock_plus_weeks": stock_plus_weeks,
        "gap": gap,
        "total_plan_ahead": total_plan_ahead,
        "total_available": total_available,
    }

def calc_remaining_to_order(history, stock, pipeline):
    total_plan = sum(COMMERCIAL_PLAN)
    total_sold = sum(h["sold"] for h in history)
    in_transit = sum(int(o["qty"] * (1 - o["defect_rate"])) for o in pipeline)
    covered = total_sold + stock + in_transit
    remaining = total_plan - covered

    return {
        "total_plan": total_plan,
        "total_sold": total_sold,
        "stock": stock,
        "in_transit": in_transit,
        "covered": covered,
        "remaining": remaining,
        "coverage_pct": covered / total_plan * 100 if total_plan > 0 else 0,
    }

# ---------- ПРОГНОЗ: УСПЕЕТЕ ЛИ ЗАКАЗАТЬ ----------
def calc_order_timing(week, remaining_to_order):
    """
    Считает, успевает ли игрок заказать оставшийся объём с учётом lead time.
    Возвращает по каждому поставщику: последнюю неделю заказа и статус.
    """
    weeks_left = SEASON_WEEKS - week
    min_lead = min(s["lead_time"] for s in SUPPLIERS.values())
    result = {
        "weeks_left": weeks_left,
        "min_lead": min_lead,
        "remaining": remaining_to_order,
        "suppliers": {},
        "status": None,
        "message": "",
    }

    for name, s in SUPPLIERS.items():
        last_order_week = SEASON_WEEKS - s["lead_time"]
        can_still_order = week <= last_order_week
        result["suppliers"][name] = {
            "lead_time": s["lead_time"],
            "last_order_week": last_order_week,
            "can_still_order": can_still_order,
        }

    if remaining_to_order <= 0:
        result["status"] = "ok"
        result["message"] = "План сезона уже покрыт — заказывать не нужно."
    elif weeks_left < min_lead:
        result["status"] = "too_late"
        result["message"] = (
            f"🔴 Уже не успеваете: до конца сезона {weeks_left} нед, "
            f"а минимальный lead time — {min_lead} нед. Заказ не придёт вовремя."
        )
    else:
        fast_suppliers = [
            n for n, v in result["suppliers"].items() if v["can_still_order"]
        ]
        slow_suppliers = [
            n for n, v in result["suppliers"].items() if not v["can_still_order"]
        ]
        if slow_suppliers and fast_suppliers:
            result["status"] = "partial"
            result["message"] = (
                f"🟡 Успеваете только у: {', '.join(fast_suppliers)}. "
                f"Уже поздно для: {', '.join(slow_suppliers)}."
            )
        elif fast_suppliers:
            result["status"] = "ok"
            result["message"] = (
                f"🟢 Успеваете заказать у: {', '.join(fast_suppliers)} "
                f"(до конца сезона {weeks_left} нед)."
            )
        else:
            result["status"] = "too_late"
            result["message"] = (
                f"🔴 Уже не успеваете ни у одного поставщика: "
                f"до конца сезона {weeks_left} нед."
            )

    return result

def build_full_plan_table(week, history):
    rows = []
    history_by_week = {h["week"]: h for h in history}

    for w in range(SEASON_WEEKS):
        plan_w = COMMERCIAL_PLAN[w]
        week_num = w + 1
        h = history_by_week.get(week_num)

        if h:
            fact = h["sold"]
            fulfilled = h["fulfilled"]
            stock = h["stock"]
            cash = h["cash"]
            status = "✅ Выполнен" if fulfilled >= plan_w else f"⚠️ -{plan_w - fulfilled} шт"
        else:
            fact = None
            fulfilled = None
            stock = None
            cash = None
            status = "—" if w > week else "⏳ Текущая"

        rows.append({
            "Неделя": week_num,
            "План, шт": plan_w,
            "Факт продаж, шт": fact,
            "Закрыто плана, шт": fulfilled,
            "Остаток, шт": stock,
            "Деньги, ₽": cash,
            "Статус": status,
        })

    return pd.DataFrame(rows)

def next_week(order_qty, supplier_name):
    supplier = SUPPLIERS[supplier_name]
    week = st.session_state.week
    week_warnings = []

    if order_qty > 0:
        if order_qty < supplier["moq"]:
            week_warnings.append(f"Заказ меньше MOQ ({supplier['moq']} шт). Заказ не принят.")
        else:
            cost = order_qty * supplier["price"]
            if cost > st.session_state.cash:
                week_warnings.append("Недостаточно денег на заказ.")
            else:
                st.session_state.cash -= cost
                actual_lead = supplier["lead_time"]
                if np.random.random() > supplier["reliability"]:
                    actual_lead += supplier["delay_weeks"]
                st.session_state.pipeline.append({
                    "arrival_week": week + actual_lead,
                    "qty": order_qty,
                    "supplier": supplier_name,
                    "defect_rate": supplier["defect_rate"],
                })

    arrived_qty = 0
    remaining = []
    for order in st.session_state.pipeline:
        if order["arrival_week"] <= week:
            good_qty = int(order["qty"] * (1 - order["defect_rate"]))
            arrived_qty += good_qty
        else:
            remaining.append(order)
    st.session_state.pipeline = remaining
    st.session_state.stock += arrived_qty

    demand = get_demand(week)
    plan_week = COMMERCIAL_PLAN[week]
    stock_before_sale = st.session_state.stock
    sold = min(st.session_state.stock, demand)
    lost = demand - sold
    st.session_state.stock -= sold
    st.session_state.cash += sold * SELL_PRICE
    st.session_state.lost_sales += lost
    st.session_state.total_sold += sold

    fulfilled = min(sold, plan_week)
    st.session_state.plan_fulfilled += fulfilled

    holding = st.session_state.stock * supplier["price"] * HOLDING_COST_RATE
    st.session_state.cash -= holding

    st.session_state.history.append({
        "week": week + 1,
        "plan": plan_week,
        "demand": demand,
        "sold": sold,
        "lost": lost,
        "stock": st.session_state.stock,
        "cash": st.session_state.cash,
        "arrived": arrived_qty,
        "fulfilled": fulfilled,
    })

    shortfall = max(0, plan_week - fulfilled)
    if shortfall > 0:
        if stock_before_sale < plan_week:
            reason = f"запаса было меньше плана ({stock_before_sale} шт против {plan_week} шт)"
        else:
            reason = "спрос превысил план, товара не хватило"
        summary = (
            f"Неделя {week + 1}: план {plan_week} шт, закрыто {fulfilled} шт, "
            f"недовыполнение {shortfall} шт — {reason}."
        )
    else:
        summary = (
            f"Неделя {week + 1}: план {plan_week} шт закрыт полностью "
            f"(продано {sold} шт)."
        )

    st.session_state.last_week_summary = {
        "text": summary,
        "warnings": week_warnings,
        "arrived": arrived_qty,
    }

    st.session_state.week += 1

# ============================================================
# СРАВНЕНИЕ СТРАТЕГИЙ (BACKTEST)
# ============================================================
def simulate_strategy(strategy_name, seed=42):
    """
    Прогоняет сезон по заданной стратегии на фиксированном seed,
    чтобы сравнение было честным.
    """
    rng = np.random.default_rng(seed)
    stock = INITIAL_STOCK
    cash = INITIAL_CASH
    pipeline = []
    history = []
    plan_fulfilled = 0

    for week in range(SEASON_WEEKS):
        plan_week = COMMERCIAL_PLAN[week]

        # ---- Решение стратегии ----
        order_qty = 0
        supplier_name = None

        if strategy_name == "Только фабрика, впритык":
            # Заказываем, когда остаток < план следующей недели * 2
            if stock < plan_week * 2:
                order_qty = SUPPLIERS["Китайская фабрика"]["moq"]
                supplier_name = "Китайская фабрика"

        elif strategy_name == "Только трейдер":
            if stock < plan_week * 2:
                order_qty = SUPPLIERS["Трейдер"]["moq"]
                supplier_name = "Трейдер"

        elif strategy_name == "Баланс: фабрика + трейдер":
            # Фабрика заранее на 4 недели, трейдер — если срочно
            ahead = sum(COMMERCIAL_PLAN[week:week + 4])
            if stock < ahead and week + 8 <= SEASON_WEEKS:
                order_qty = SUPPLIERS["Китайская фабрика"]["moq"]
                supplier_name = "Китайская фабрика"
            elif stock < plan_week * 2 and week + 4 <= SEASON_WEEKS:
                order_qty = SUPPLIERS["Трейдер"]["moq"]
                supplier_name = "Трейдер"

        # ---- Размещение заказа ----
        if order_qty > 0 and supplier_name:
            s = SUPPLIERS[supplier_name]
            cost = order_qty * s["price"]
            if cost <= cash and order_qty >= s["moq"]:
                cash -= cost
                actual_lead = s["lead_time"]
                if rng.random() > s["reliability"]:
                    actual_lead += s["delay_weeks"]
                pipeline.append({
                    "arrival_week": week + actual_lead,
                    "qty": order_qty,
                    "defect_rate": s["defect_rate"],
                    "price": s["price"],
                })

        # ---- Поступление ----
        arrived = 0
        remaining = []
        for o in pipeline:
            if o["arrival_week"] <= week:
                arrived += int(o["qty"] * (1 - o["defect_rate"]))
            else:
                remaining.append(o)
        pipeline = remaining
        stock += arrived

        # ---- Продажи ----
        demand = get_demand(week, rng)
        sold = min(stock, demand)
        stock -= sold
        cash += sold * SELL_PRICE

        fulfilled = min(sold, plan_week)
        plan_fulfilled += fulfilled

        # ---- Хранение ----
        holding = stock * 800 * HOLDING_COST_RATE
        cash -= holding

        history.append({
            "week": week + 1,
            "plan": plan_week,
            "sold": sold,
            "stock": stock,
            "cash": cash,
            "fulfilled": fulfilled,
        })

    total_plan = sum(COMMERCIAL_PLAN)
    fill_rate = plan_fulfilled / total_plan * 100
    profit = cash - INITIAL_CASH
    oos_weeks = sum(1 for h in history if h["fulfilled"] < h["plan"])
    final_stock = history[-1]["stock"]

    return {
        "Стратегия": strategy_name,
        "Прибыль, ₽": profit,
        "Fill rate, %": fill_rate,
        "Недель с недовыполнением": oos_weeks,
        "Остаток на конец, шт": final_stock,
    }

def run_strategy_comparison():
    strategies = [
        "Только фабрика, впритык",
        "Только трейдер",
        "Баланс: фабрика + трейдер",
    ]
    results = [simulate_strategy(s) for s in strategies]
    return pd.DataFrame(results)

# ============================================================
# ПОДСКАЗКИ
# ============================================================
def get_hints(week, stock, pipeline, plan_week, cash):
    hints = []
    in_transit = sum(o["qty"] for o in pipeline)

    if stock < plan_week:
        hints.append(
            f"⚠️ На складе {stock} шт, а план коммерции на эту неделю — {plan_week} шт. "
            f"Если не хватит товара, план будет недовыполнен (fill rate упадёт)."
        )
    else:
        hints.append(
            f"✅ На складе {stock} шт — это покрывает план недели ({plan_week} шт)."
        )

    if in_transit == 0 and stock < plan_week * 2:
        hints.append(
            "📦 В пути нет заказов, а запаса меньше чем на 2 недели плана. "
            "Подумайте о заказе — с учётом lead time поставщика."
        )
    elif in_transit > 0:
        next_arrival = min(pipeline, key=lambda o: o["arrival_week"])
        weeks_left = next_arrival["arrival_week"] - week
        hints.append(
            f"🚚 Ближайшая поставка: {next_arrival['qty']} шт через {weeks_left} нед. "
            f"Успеет ли она к пику плана?"
        )

    season = SEASON_FACTOR[min(week, len(SEASON_FACTOR) - 1)]
    if season >= 1.5:
        hints.append(
            "🔥 Пик сезона. Out-of-stock сейчас стоит дороже всего — "
            "проверьте, хватит ли запаса и заказов в пути."
        )
    elif season <= 0.5:
        hints.append(
            "❄️ Низкий сезон. Избегайте избыточного запаса — "
            "он замораживает капитал и платит за хранение."
        )

    if cash < 500_000:
        hints.append(
            "💰 Денег осталось мало. Учтите: оплата заказа происходит сразу, "
            "а выручка — только после продажи."
        )

    hints.append(
        "🏭 Помните: фабрика дешевле, но lead time 8 нед; трейдер быстрее, "
        "но дороже и с браком 4%. Выбор поставщика влияет на fill rate."
    )

    cov = calc_coverage(week, stock, pipeline)
    if cov["stock_plus_weeks"] < 2:
        hints.append(
            f"🎯 Остаток + заказы покрывают план только на {cov['stock_plus_weeks']} нед. "
            f"До конца сезона не хватает {cov['gap']:,} шт плана. "
            f"Учтите lead time — заказ придёт не сразу."
        )
    elif cov["gap"] == 0:
        hints.append(
            f"🎯 Доступного товара (остаток + заказы) хватает на весь оставшийся план. "
            f"Следите за избытком — лишний запас замораживает капитал."
        )

    rem = calc_remaining_to_order(st.session_state.history, stock, pipeline)
    if rem["remaining"] > 0:
        hints.append(
            f"📦 До покрытия плана сезона осталось заказать {rem['remaining']:,} шт. "
            f"Сейчас покрыто {rem['coverage_pct']:.1f}% плана."
        )
    elif rem["remaining"] < 0:
        hints.append(
            f"📦 Избыток {abs(rem['remaining']):,} шт сверх плана сезона — "
            f"запас + заказы превышают план. Риск замороженного капитала."
        )

    # Подсказка про timing
    timing = calc_order_timing(week, rem["remaining"])
    if timing["remaining"] > 0:
        hints.append(f"⏱ {timing['message']}")

    return hints

# ============================================================
# UI
# ============================================================
st.set_page_config(page_title="Relppy — тренажёр закупщика", layout="wide")

with st.sidebar:
    st.header("Настройки")
    show_hints = st.checkbox("💡 Показывать подсказки", value=True)
    st.caption("Подсказки объясняют, на что смотреть, но не дают готовый ответ.")

st.title("📦 Relppy — тренажёр решений по пополнению")
st.caption("Коммерция задаёт план продаж · вы обеспечиваете его поставками")

with st.expander("📋 План продаж от коммерции на сезон (вход, не меняется)", expanded=False):
    plan_df = pd.DataFrame({
        "Неделя": range(1, SEASON_WEEKS + 1),
        "План, шт": COMMERCIAL_PLAN,
    })
    st.dataframe(plan_df, use_container_width=True)
    st.caption(f"Итого план за сезон: {sum(COMMERCIAL_PLAN):,} шт")

# ---------- КОНЕЦ ИГРЫ ----------
if st.session_state.week >= SEASON_WEEKS:
    st.success("Сезон завершён! Итоги ниже.")
    df = pd.DataFrame(st.session_state.history)
    total_profit = st.session_state.cash - INITIAL_CASH
    fill_rate = st.session_state.plan_fulfilled / st.session_state.plan_total * 100
    oos_weeks = (df["fulfilled"] < df["plan"]).sum()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Итоговая прибыль", f"{total_profit:,.0f} ₽")
    col2.metric("Исполнение плана (fill rate)", f"{fill_rate:.1f}%")
    col3.metric("Недель с недовыполнением плана", f"{oos_weeks} / {SEASON_WEEKS}")
    col4.metric("Остаток на конец", f"{st.session_state.stock:,} шт")

    rem_final = calc_remaining_to_order(
        st.session_state.history,
        st.session_state.stock,
        st.session_state.pipeline,
    )
    if rem_final["remaining"] > 0:
        st.error(
            f"🔴 План сезона не покрыт: не хватило {rem_final['remaining']:,} шт "
            f"(покрыто {rem_final['coverage_pct']:.1f}%)"
        )
    elif rem_final["remaining"] < 0:
        st.warning(
            f"🟡 Избыток {abs(rem_final['remaining']):,} шт сверх плана "
            f"(покрыто {rem_final['coverage_pct']:.1f}%)"
        )
    else:
        st.success(
            f"🟢 План сезона покрыт полностью "
            f"(покрыто {rem_final['coverage_pct']:.1f}%)"
        )

    st.plotly_chart(px.line(df, x="week", y=["plan", "sold", "stock"],
                            title="План коммерции / Продажи / Остаток"),
                    use_container_width=True)
    st.plotly_chart(px.line(df, x="week", y="cash", title="Денежный поток"),
                    use_container_width=True)

    st.subheader("🎯 Покрытие плана в динамике")
    df["coverage_stock"] = df.apply(
        lambda r: calc_coverage(int(r["week"]) - 1, int(r["stock"]), [])["stock_only_weeks"],
        axis=1,
    )
    st.plotly_chart(
        px.line(df, x="week", y="coverage_stock",
                title="На сколько недель плана хватало остатка (по неделям)"),
        use_container_width=True,
    )

    st.subheader("🗓 Итоговый план по всему периоду")
    full_df = build_full_plan_table(SEASON_WEEKS, st.session_state.history)
    st.dataframe(full_df, use_container_width=True, height=400)

    csv = full_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Скачать итоговый план в CSV",
        data=csv,
        file_name="relppy_plan_final.csv",
        mime="text/csv",
    )

    # ---------- СРАВНЕНИЕ СТРАТЕГИЙ НА ФИНАЛЕ ----------
    st.subheader("⚖️ Сравнение стратегий (backtest на одном seed)")
    st.caption(
        "Три стратегии прогнаны на одинаковом случайном seed — "
        "чтобы сравнение было честным."
    )
    comp_df = run_strategy_comparison()
    st.dataframe(comp_df.style.format({
        "Прибыль, ₽": "{:,.0f}",
        "Fill rate, %": "{:.1f}",
        "Остаток на конец, шт": "{:,.0f}",
    }), use_container_width=True)

    if show_hints:
        st.info(
            "💡 Как читать итоги: fill rate показывает, какую долю плана коммерции "
            "вы закрыли товаром. Сравнение стратегий показывает, что "
            "«только фабрика впритык» проигрывает по fill rate, "
            "«только трейдер» — по прибыли, а баланс даёт лучший компромисс."
        )

    if st.button("Начать заново"):
        init_state()
        st.rerun()
    st.stop()

# ---------- РАЗБОР ПРОШЛОЙ НЕДЕЛИ ----------
if st.session_state.last_week_summary:
    s = st.session_state.last_week_summary
    st.subheader("📊 Разбор прошлой недели")
    st.info(s["text"])
    if s["arrived"] > 0:
        st.success(f"📥 Поступило на склад: {s['arrived']} шт.")
    for w in s["warnings"]:
        st.warning(w)

# ---------- ТЕКУЩИЕ МЕТРИКИ ----------
week = st.session_state.week
cov = calc_coverage(week, st.session_state.stock, st.session_state.pipeline)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Неделя", f"{week + 1} / {SEASON_WEEKS}")
col2.metric("Остаток", f"{st.session_state.stock:,} шт")
col3.metric("Деньги", f"{st.session_state.cash:,.0f} ₽")
col4.metric("В пути", f"{sum(o['qty'] for o in st.session_state.pipeline):,} шт")
col5.metric("План недели", f"{COMMERCIAL_PLAN[week]:,} шт")

# ---------- ПОКРЫТИЕ ПЛАНА ----------
st.subheader("🎯 Покрытие плана коммерции")
c1, c2, c3 = st.columns(3)
c1.metric(
    "Остаток покрывает",
    f"{cov['stock_only_weeks']} нед плана",
    help="На сколько недель вперёд хватает текущего запаса при плановом спросе.",
)
c2.metric(
    "Остаток + заказы покрывают",
    f"{cov['stock_plus_weeks']} нед плана",
    help="На сколько недель хватает запаса с учётом всех заказов в пути.",
)
c3.metric(
    "Разрыв с планом до конца сезона",
    f"{cov['gap']:,} шт",
    help="Сколько плана не покрыто доступным товаром (остаток + заказы) до конца сезона.",
)

# ---------- ИСПОЛНЕНИЕ ПЛАНА ----------
st.subheader("📈 Исполнение плана")
progress = st.session_state.plan_fulfilled / st.session_state.plan_total
st.progress(
    min(progress, 1.0),
    text=f"Закрыто {st.session_state.plan_fulfilled:,} из {st.session_state.plan_total:,} шт плана ({progress*100:.1f}%)",
)

# ---------- СКОЛЬКО ОСТАЛОСЬ ЗАКАЗАТЬ ----------
st.subheader("📦 Сколько осталось заказать от плана продаж")

rem = calc_remaining_to_order(
    st.session_state.history,
    st.session_state.stock,
    st.session_state.pipeline,
)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("План сезона", f"{rem['total_plan']:,} шт")
c2.metric("Продано", f"{rem['total_sold']:,} шт")
c3.metric("Остаток", f"{rem['stock']:,} шт")
c4.metric("В пути", f"{rem['in_transit']:,} шт")
c5.metric("Покрыто", f"{rem['covered']:,} шт", help="Продано + остаток + в пути")

if rem["remaining"] > 0:
    st.error(
        f"🔴 Осталось заказать: **{rem['remaining']:,} шт** "
        f"(покрыто {rem['coverage_pct']:.1f}% плана сезона)"
    )
elif rem["remaining"] < 0:
    st.warning(
        f"🟡 Избыток: **{abs(rem['remaining']):,} шт** сверх плана "
        f"(покрыто {rem['coverage_pct']:.1f}% плана сезона)"
    )
else:
    st.success(
        f"🟢 План сезона покрыт полностью "
        f"(покрыто {rem['coverage_pct']:.1f}% плана сезона)"
    )

st.progress(
    min(rem["coverage_pct"] / 100, 1.0),
    text=f"Покрытие плана сезона: {rem['coverage_pct']:.1f}%",
)

# ---------- ПРОГНОЗ: УСПЕЕТЕ ЛИ ЗАКАЗАТЬ ----------
st.subheader("⏱ Успеете ли заказать (с учётом lead time)")

timing = calc_order_timing(week, rem["remaining"])

c1, c2, c3 = st.columns(3)
c1.metric("До конца сезона", f"{timing['weeks_left']} нед")
c2.metric("Мин. lead time", f"{timing['min_lead']} нед")
c3.metric("Осталось заказать", f"{max(0, timing['remaining']):,} шт")

if timing["status"] == "ok":
    st.success(timing["message"])
elif timing["status"] == "partial":
    st.warning(timing["message"])
elif timing["status"] == "too_late":
    st.error(timing["message"])

# Таблица по поставщикам
timing_rows = []
for name, v in timing["suppliers"].items():
    timing_rows.append({
        "Поставщик": name,
        "Lead time, нед": v["lead_time"],
        "Последняя неделя заказа": v["last_order_week"],
        "Ещё успеваете?": "✅ Да" if v["can_still_order"] else "❌ Нет",
    })
st.dataframe(pd.DataFrame(timing_rows), use_container_width=True)

# ---------- ОБЩИЙ ПЛАН ПО ВСЕМУ ПЕРИОДУ ----------
st.subheader("🗓 Общий план по всему периоду")

full_df = build_full_plan_table(st.session_state.week, st.session_state.history)

total_plan = full_df["План, шт"].sum()
total_fact = full_df["Факт продаж, шт"].dropna().sum()
total_fulfilled = full_df["Закрыто плана, шт"].dropna().sum()
total_fill = total_fulfilled / total_plan * 100 if total_plan > 0 else 0

st.caption(
    f"Итого план: {total_plan:,} шт · Факт продаж: {int(total_fact):,} шт · "
    f"Закрыто плана: {int(total_fulfilled):,} шт · Fill rate: {total_fill:.1f}%"
)

def highlight_status(row):
    if isinstance(row["Статус"], str) and row["Статус"].startswith("⚠️"):
        return ["background-color: #ffe0e0"] * len(row)
    if row["Статус"] == "✅ Выполнен":
        return ["background-color: #e0ffe0"] * len(row)
    return [""] * len(row)

styled_df = full_df.style.apply(highlight_status, axis=1).format(
    {
        "Факт продаж, шт": lambda x: f"{int(x):,}" if pd.notna(x) else "—",
        "Закрыто плана, шт": lambda x: f"{int(x):,}" if pd.notna(x) else "—",
        "Остаток, шт": lambda x: f"{int(x):,}" if pd.notna(x) else "—",
        "Деньги, ₽": lambda x: f"{x:,.0f}" if pd.notna(x) else "—",
    }
)

st.dataframe(styled_df, use_container_width=True, height=400)

csv = full_df.to_csv(index=False).encode("utf-8")
st.download_button(
    "📥 Скачать план в CSV",
    data=csv,
    file_name="relppy_plan.csv",
    mime="text/csv",
)

if not full_df.dropna(subset=["Факт продаж, шт"]).empty:
    fig = px.bar(
        full_df,
        x="Неделя",
        y=["План, шт", "Факт продаж, шт"],
        barmode="group",
        title="План коммерции vs Факт продаж по неделям",
    )
    st.plotly_chart(fig, use_container_width=True)

# ---------- СРАВНЕНИЕ СТРАТЕГИЙ (ДОСТУПНО В ЛЮБОЙ МОМЕНТ) ----------
st.subheader("⚖️ Сравнение стратегий (backtest на одном seed)")
st.caption(
    "Три стратегии прогнаны на одинаковом случайном seed — "
    "чтобы сравнение было честным. Это ответ на вопрос "
    "«какая закупочная политика лучше»."
)
comp_df = run_strategy_comparison()
st.dataframe(comp_df.style.format({
    "Прибыль, ₽": "{:,.0f}",
    "Fill rate, %": "{:.1f}",
    "Остаток на конец, шт": "{:,.0f}",
}), use_container_width=True)

# ---------- ПОДСКАЗКИ ----------
if show_hints:
    st.subheader("💡 Подсказки")
    hints = get_hints(
        week=st.session_state.week,
        stock=st.session_state.stock,
        pipeline=st.session_state.pipeline,
        plan_week=COMMERCIAL_PLAN[st.session_state.week],
        cash=st.session_state.cash,
    )
    for h in hints:
        st.info(h)

# ---------- ПАНЕЛЬ ЗАКАЗА ----------
st.subheader("Сделать заказ")
col1, col2 = st.columns(2)
with col1:
    supplier_name = st.selectbox("Поставщик", list(SUPPLIERS.keys()))
    s = SUPPLIERS[supplier_name]
    st.caption(
        f"Lead time: {s['lead_time']} нед · MOQ: {s['moq']} шт · "
        f"Цена: {s['price']} ₽ · Брак: {s['defect_rate']*100:.0f}% · "
        f"Надёжность: {s['reliability']*100:.0f}%"
    )
with col2:
    order_qty = st.number_input("Количество", min_value=0, step=100, value=s["moq"])

if st.button("Следующая неделя →", type="primary"):
    next_week(order_qty, supplier_name)
    st.rerun()

# ---------- ГРАФИКИ И ЗАКАЗЫ В ПУТИ ----------
if st.session_state.history:
    df = pd.DataFrame(st.session_state.history)
    st.plotly_chart(
        px.line(df, x="week", y=["plan", "sold", "stock"],
                title="План коммерции / Продажи / Остаток"),
        use_container_width=True,
    )
    st.plotly_chart(
        px.line(df, x="week", y="cash", title="Денежный поток"),
        use_container_width=True,
    )

if st.session_state.pipeline:
    st.subheader("Заказы в пути")
    st.dataframe(pd.DataFrame(st.session_state.pipeline), use_container_width=True)