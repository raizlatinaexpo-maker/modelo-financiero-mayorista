
import streamlit as st
import sqlite3
import pandas as pd
import requests
import os
from datetime import date, datetime
from io import BytesIO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "raiz_latina.db")

st.set_page_config(page_title="Raíz Latina Mayorista", page_icon="🌎", layout="wide")

# ---------------- DATABASE ----------------
def get_conn():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        country TEXT,
        email TEXT,
        phone TEXT,
        notes TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE NOT NULL,
        client_id INTEGER NOT NULL,
        country TEXT NOT NULL,
        order_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'POR CONFIRMAR',
        products_amount_original REAL NOT NULL,
        products_currency TEXT NOT NULL,
        shipping_charged_original REAL NOT NULL DEFAULT 0,
        shipping_currency TEXT NOT NULL,
        sale_rate REAL NOT NULL,
        products_sale_cop REAL NOT NULL,
        shipping_sale_cop REAL NOT NULL,
        product_cost_cop REAL NOT NULL,
        estimated_profit_cop REAL NOT NULL,
        real_shipping_original REAL,
        real_shipping_currency TEXT,
        real_shipping_rate REAL,
        real_shipping_cop REAL,
        notes TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(client_id) REFERENCES clients(id)
    );

    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        payment_date TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL,
        rate REAL NOT NULL,
        cop_amount REAL NOT NULL,
        method TEXT,
        reference TEXT,
        notes TEXT,
        FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_date TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL,
        rate REAL NOT NULL,
        cop_amount REAL NOT NULL,
        method TEXT,
        supplier TEXT,
        notes TEXT,
        created_at TEXT NOT NULL
    );
    """)
    defaults = {
        "eur_cop": "3600",
        "usd_cop": "3900",
        "product_margin": "25",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
    conn.commit()
    return conn

conn = init_db()

existing_client_cols = {row[1] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
for col, definition in [("city","TEXT"),("address","TEXT"),("postal_code","TEXT")]:
    if col not in existing_client_cols:
        conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {definition}")
conn.commit()

# ---------------- HELPERS ----------------
def setting(key):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return float(row[0]) if row else 0.0

def set_setting(key, value):
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, str(value)))
    conn.commit()

def fmt_cop(x):
    return f"${x:,.0f}".replace(",", ".")

def fmt_money(x, cur):
    return f"{cur} {x:,.2f}"

def get_reference_rates():
    """Reference rates only. Stored transaction rates are never overwritten."""
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=COP,EUR",
            timeout=8
        )
        r.raise_for_status()
        data = r.json()["rates"]
        usd_cop = float(data["COP"])
        usd_eur = float(data["EUR"])
        if usd_cop > 0 and usd_eur > 0:
            return {"USD": usd_cop, "EUR": usd_cop / usd_eur,
                    "source": "Frankfurter", "date": data.get("date", "")}
    except Exception:
        pass
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8)
        r.raise_for_status()
        data = r.json()["rates"]
        usd_cop = float(data["COP"])
        usd_eur = float(data["EUR"])
        if usd_cop > 0 and usd_eur > 0:
            return {"USD": usd_cop, "EUR": usd_cop / usd_eur,
                    "source": "ExchangeRate API", "date": data.get("time_last_update_utc", "")}
    except Exception:
        pass
    return {"USD": None, "EUR": None, "source": None, "date": ""}


def excel_bytes(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Reporte")
    return output.getvalue()

def order_payment_summary(order_id):
    row = conn.execute("""
        SELECT
            COALESCE(SUM(cop_amount),0),
            COALESCE(SUM(CASE WHEN currency='EUR' THEN amount ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN currency='USD' THEN amount ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN currency='COP' THEN amount ELSE 0 END),0)
        FROM payments WHERE order_id=?
    """, (order_id,)).fetchone()
    return row

# ---------------- SIDEBAR ----------------
st.sidebar.title("🌎 Raíz Latina")
st.sidebar.caption("Sistema financiero y logístico mayorista")

page = st.sidebar.radio(
    "Módulo",
    ["Dashboard", "Nuevo pedido", "Pedidos", "Envíos pendientes", "Clientes", "Gastos", "Reportes", "Configuración"]
)

# ---------------- DASHBOARD ----------------
if page == "Dashboard":
    st.title("Dashboard")

    ref = get_reference_rates()

    total_sales = conn.execute("""
        SELECT COALESCE(SUM(products_sale_cop + shipping_sale_cop),0)
        FROM orders
    """).fetchone()[0]

    total_product_cost = conn.execute("""
        SELECT COALESCE(SUM(product_cost_cop),0)
        FROM orders
    """).fetchone()[0]

    # Shipping cost: use the real shipping cost when it has been updated;
    # otherwise keep the charged shipping as the provisional cost.
    total_shipping_cost = conn.execute("""
        SELECT COALESCE(SUM(
            CASE
                WHEN real_shipping_cop IS NOT NULL THEN real_shipping_cop
                ELSE shipping_sale_cop
            END
        ),0)
        FROM orders
    """).fetchone()[0]

    total_expenses = conn.execute("""
        SELECT COALESCE(SUM(cop_amount),0)
        FROM expenses
    """).fetchone()[0]

    estimated_result = (
        total_sales
        - total_product_cost
        - total_shipping_cost
        - total_expenses
    )
    estimated_margin = (
        estimated_result / total_sales * 100
        if total_sales > 0 else 0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ventas acumuladas", fmt_cop(total_sales))
    c2.metric("Costo productos", fmt_cop(total_product_cost))
    c3.metric("Costo envío", fmt_cop(total_shipping_cost))
    c4.metric("Gastos registrados", fmt_cop(total_expenses))

    st.divider()
    r1, r2 = st.columns(2)
    r1.metric("Resultado estimado", fmt_cop(estimated_result))
    r2.metric("Utilidad estimada", f"{estimated_margin:.2f}%")

    st.caption(
        "Resultado = ventas acumuladas − costo de productos − costo de envío − gastos registrados. "
        "Cuando un pedido tiene envío real actualizado, el Dashboard usa ese costo; "
        "los pedidos pendientes conservan provisionalmente el envío cobrado."
    )

    st.divider()
    a, b, c = st.columns(3)
    a.metric("EUR referencia", fmt_cop(ref["EUR"]) if ref["EUR"] else "No disponible")
    b.metric("USD referencia", fmt_cop(ref["USD"]) if ref["USD"] else "No disponible")
    pending = conn.execute("""
        SELECT COUNT(*) FROM orders
        WHERE status IN ('PAGO PARCIAL','POR CONFIRMAR','MERCANCÍA SOLICITADA','LISTO PARA ENVÍO')
    """).fetchone()[0]
    c.metric("Envíos pendientes", pending)

    st.subheader("Ventas por mes")
    df = pd.read_sql_query("""
        SELECT substr(order_date,1,7) AS Mes,
               SUM(products_sale_cop + shipping_sale_cop) AS Ventas
        FROM orders
        GROUP BY substr(order_date,1,7)
        ORDER BY Mes
    """, conn)
    if not df.empty:
        df["Ventas"] = df["Ventas"].round(0)
        st.bar_chart(df.set_index("Mes"))
    else:
        st.info("Todavía no hay ventas registradas.")

# ---------------- NEW ORDER ----------------
elif page == "Nuevo pedido":
    st.title("Nuevo pedido")

    clients = pd.read_sql_query("SELECT id, name, country FROM clients ORDER BY name", conn)

    if clients.empty:
        st.warning("Primero crea al menos un cliente en el módulo Clientes.")
    else:
        ref = get_reference_rates()
        with st.form("new_order"):
            order_number_number = st.number_input(
                "Número de pedido",
                min_value=1,
                step=1,
                format="%d",
                help="Escribe únicamente el número. El sistema agregará automáticamente 'Orden #'."
            )
            order_date = st.date_input("Fecha", value=date.today())
            st.caption("Busca escribiendo el nombre o empresa y luego selecciona el cliente.")
            client_id = st.selectbox(
                "Buscar cliente",
                clients["id"].tolist(),
                format_func=lambda x: (
                    f"{clients.loc[clients.id == x, 'name'].iloc[0]} — "
                    f"{clients.loc[clients.id == x, 'country'].iloc[0] or 'Sin país'}"
                )
            )
            client_country = clients.loc[clients.id == client_id, "country"].iloc[0]
            country = st.text_input("País de destino", value=client_country or "")

            st.subheader("Venta de productos")
            currency = st.selectbox("Divisa de productos", ["EUR", "USD", "COP"])
            products_amount = st.number_input("Valor de productos", min_value=0.0, step=10.0)

            configured_rate = {"EUR": setting("eur_cop"), "USD": setting("usd_cop"), "COP": 1}[currency]
            auto_rate = {"EUR": ref["EUR"], "USD": ref["USD"], "COP": 1}[currency]

            ref_label = (
                f"{auto_rate:,.2f} COP/{currency} ({ref.get('source', 'automática')})"
                if auto_rate else "No disponible"
            )
            st.caption(
                f"Tasa configurada Raíz Latina: {configured_rate:,.2f} COP/{currency}  |  "
                f"Tasa automática de referencia: {ref_label}"
            )
            if not auto_rate:
                st.warning(
                    "No se pudo consultar la tasa automática en este momento. "
                    "Puedes continuar usando la tasa configurada de Raíz Latina."
                )
            sale_rate = st.number_input(
                "Tasa utilizada para la venta (editable)",
                min_value=0.0001,
                value=float(configured_rate),
                step=1.0
            )

            st.subheader("Envío cobrado al cliente")
            shipping_currency = st.selectbox(
                "Divisa del envío",
                ["EUR", "USD", "COP"],
                key="shipping_currency"
            )
            shipping_charged = st.number_input(
                "Valor de envío cobrado",
                min_value=0.0,
                step=5.0
            )
            shipping_rate = {"EUR": setting("eur_cop"), "USD": setting("usd_cop"), "COP": 1}[shipping_currency]
            shipping_sale_cop = shipping_charged * shipping_rate

            st.subheader("Pago recibido")
            payment_type = st.selectbox(
                "Tipo de pago",
                ["Pago completo", "Abono"],
                help="Selecciona Abono cuando el cliente paga sólo una parte y completará el pago después."
            )
            st.caption(
                "El pago queda registrado como movimiento. Si seleccionas Abono, el pedido quedará "
                "automáticamente como PAGO PARCIAL en Envíos pendientes."
            )
            payment_method = st.selectbox(
                "Medio de pago",
                ["PayPal","Wise","Transferencia","Nequi","Stripe","Efectivo","Otro"]
            )
            payment_currency = st.selectbox(
                "Divisa del pago",
                ["EUR","USD","COP"],
                key="new_order_payment_currency"
            )
            payment_amount = st.number_input(
                "Monto recibido",
                min_value=0.0,
                step=10.0,
                help="Si el pedido fue pagado en más de una divisa, este es el primer movimiento."
            )
            payment_default_rate = {
                "EUR": setting("eur_cop"),
                "USD": setting("usd_cop"),
                "COP": 1
            }[payment_currency]
            payment_rate = st.number_input(
                "Tasa real del pago",
                min_value=0.0001,
                value=float(payment_default_rate),
                step=1.0
            )
            payment_reference = st.text_input("Referencia del pago")
            payment_notes = st.text_input("Notas del pago")

            st.subheader("Observaciones")
            notes = st.text_area("Notas del pedido")

            submitted = st.form_submit_button("Guardar pedido", type="primary")

        if submitted:
            order_number = f"Orden #{int(order_number_number)}"
            existing = conn.execute(
                "SELECT 1 FROM orders WHERE order_number = ? LIMIT 1",
                (order_number,)
            ).fetchone()
            if existing:
                st.error(f"El número {int(order_number_number)} ya está registrado como {order_number}. Usa otro número de pedido.")
                st.stop()

            product_sale_cop = products_amount * sale_rate
            product_cost_cop = product_sale_cop * (1 - setting("product_margin") / 100)
            estimated_profit = product_sale_cop - product_cost_cop

            total_sale_cop = product_sale_cop + shipping_sale_cop
            payment_cop = payment_amount * payment_rate

            if payment_amount <= 0:
                st.error("El monto recibido debe ser mayor que 0.")
                st.stop()
            if payment_type == "Pago completo" and payment_cop + 0.01 < total_sale_cop:
                st.error(
                    f"Marcaste Pago completo, pero el pago registrado equivale a {fmt_cop(payment_cop)} "
                    f"y la venta total es {fmt_cop(total_sale_cop)}. Si sólo pagó una parte, selecciona Abono."
                )
                st.stop()

            try:
                cur = conn.execute("""
                    INSERT INTO orders (
                        order_number, client_id, country, order_date, status,
                        products_amount_original, products_currency,
                        shipping_charged_original, shipping_currency,
                        sale_rate, products_sale_cop, shipping_sale_cop,
                        product_cost_cop, estimated_profit_cop, notes, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    order_number.strip(), client_id, country.strip(), str(order_date),
                    "PAGO PARCIAL" if payment_type == "Abono" else "POR CONFIRMAR",
                    products_amount, currency,
                    shipping_charged, shipping_currency,
                    sale_rate, product_sale_cop, shipping_sale_cop,
                    product_cost_cop, estimated_profit, notes,
                    datetime.now().isoformat(timespec="seconds")
                ))
                order_id = cur.lastrowid

                conn.execute("""
                    INSERT INTO payments(
                        order_id, payment_date, amount, currency, rate, cop_amount,
                        method, reference, notes
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    order_id, str(order_date), payment_amount, payment_currency,
                    payment_rate, payment_cop, payment_method,
                    payment_reference.strip(), payment_notes.strip()
                ))
                conn.commit()

                st.success(f"Pedido {order_number} creado correctamente.")
                if payment_type == "Abono":
                    saldo = max(total_sale_cop - payment_cop, 0)
                    st.warning(
                        f"ABONO registrado: {fmt_cop(payment_cop)}. "
                        f"Saldo pendiente estimado: {fmt_cop(saldo)}. "
                        "El pedido aparece como PAGO PARCIAL en Envíos pendientes."
                    )
                else:
                    st.info("Estado inicial: POR CONFIRMAR → aparece automáticamente en Envíos pendientes.")
            except sqlite3.IntegrityError as e:
                if "orders.order_number" in str(e) or "UNIQUE constraint failed: orders.order_number" in str(e):
                    st.error(f"El número {order_number} ya está registrado. Verifica el número e intenta nuevamente.")
                else:
                    st.error(f"No se pudo guardar el pedido: {e}")

# ---------------- ORDERS ----------------
elif page == "Pedidos":
    st.title("Pedidos")
    st.caption(
        "Busca por pedido, cliente o país. Envío COP se edita directamente en la tabla "
        "una sola vez para registrar el costo real. El valor de productos y la venta total "
        "se calculan automáticamente."
    )

    df = pd.read_sql_query("""
        SELECT o.id, o.order_number AS Pedido, c.name AS Cliente, o.country AS País,
               o.order_date AS Fecha,
               o.products_sale_cop AS "Valor productos",
               CASE
                   WHEN o.real_shipping_cop IS NULL THEN o.shipping_sale_cop
                   ELSE o.real_shipping_cop
               END AS "Envío COP",
               (o.products_sale_cop + o.shipping_sale_cop) AS "Venta total",
               CASE
                   WHEN o.real_shipping_cop IS NULL THEN 'NO ACTUALIZADO'
                   ELSE 'ACTUALIZADO'
               END AS "Envío real"
        FROM orders o
        JOIN clients c ON c.id=o.client_id
        ORDER BY o.order_date DESC, o.id DESC
    """, conn)

    if df.empty:
        st.info("No hay pedidos registrados.")
    else:
        search = st.text_input(
            "🔎 Buscar pedido, cliente o país",
            placeholder="Escribe pedido, cliente o país..."
        )
        filtered = df.copy()
        if search.strip():
            q = search.strip()
            filtered = filtered[
                filtered["Pedido"].str.contains(q, case=False, na=False) |
                filtered["Cliente"].str.contains(q, case=False, na=False) |
                filtered["País"].str.contains(q, case=False, na=False)
            ].copy()

        if filtered.empty:
            st.info("No se encontraron pedidos.")
        else:
            original = filtered.copy()
            visible = filtered.drop(columns=["id"]).copy()

            edited = st.data_editor(
                visible,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key="orders_shipping_editor",
                disabled=["Pedido", "Cliente", "País", "Fecha", "Valor productos", "Venta total", "Envío real"],
                column_config={
                    "Valor productos": st.column_config.NumberColumn(
                        "Valor productos",
                        format="$ %.0f"
                    ),
                    "Envío COP": st.column_config.NumberColumn(
                        "Envío COP",
                        min_value=0.0,
                        step=1000.0,
                        format="$ %.0f",
                        help="Escribe aquí el costo real. Sólo puede actualizarse una vez."
                    ),
                    "Venta total": st.column_config.NumberColumn(
                        "Venta total",
                        format="$ %.0f"
                    ),
                    "Envío real": st.column_config.TextColumn("Envío real")
                }
            )

            changes = []
            for i in range(len(edited)):
                old_value = float(visible.iloc[i]["Envío COP"] or 0)
                new_value = float(edited.iloc[i]["Envío COP"] or 0)
                status = visible.iloc[i]["Envío real"]
                if new_value != old_value:
                    changes.append((i, new_value, status))

            if changes:
                invalid = [x for x in changes if x[2] == "ACTUALIZADO"]
                if invalid:
                    st.error(
                        "Un envío marcado como ACTUALIZADO no puede modificarse nuevamente. "
                        "Recarga la página para restaurar el valor."
                    )
                else:
                    # Data editor changes are persisted immediately once detected.
                    for i, new_value, _ in changes:
                        oid = int(original.iloc[i]["id"])
                        current = conn.execute(
                            "SELECT real_shipping_cop FROM orders WHERE id=?", (oid,)
                        ).fetchone()
                        if current and current[0] is None:
                            conn.execute("""
                                UPDATE orders
                                SET real_shipping_original=?,
                                    real_shipping_currency='COP',
                                    real_shipping_rate=1,
                                    real_shipping_cop=?
                                WHERE id=? AND real_shipping_cop IS NULL
                            """, (new_value, new_value, oid))
                    conn.commit()
                    st.rerun()

# ---------------- SHIPPING ----------------
elif page == "Envíos pendientes":
    st.title("📦 Envíos pendientes")
    st.caption(
        "Aquí aparecen pedidos que todavía no están ENVIADOS. "
        "Los pedidos con PAGO PARCIAL se muestran completos en rojo para identificarlos rápidamente."
    )

    df = pd.read_sql_query("""
        SELECT o.id, o.order_number AS Pedido, c.name AS Cliente, o.country AS País,
               o.order_date AS Fecha, o.status AS Estado
        FROM orders o JOIN clients c ON c.id=o.client_id
        WHERE o.status IN ('PAGO PARCIAL','POR CONFIRMAR','MERCANCÍA SOLICITADA','LISTO PARA ENVÍO')
        ORDER BY o.order_date ASC, o.id ASC
    """, conn)

    if df.empty:
        st.success("No hay envíos pendientes.")
    else:
        status_options = [
            "PAGO PARCIAL",
            "POR CONFIRMAR",
            "MERCANCÍA SOLICITADA",
            "LISTO PARA ENVÍO",
            "ENVIADO"
        ]

        h1, h2, h3, h4, h5 = st.columns([1.5, 2.5, 1.5, 1.2, 2.3])
        for h, text in zip((h1,h2,h3,h4,h5),("Pedido","Cliente","País","Fecha","Estado")):
            h.markdown(f"**{text}**")
        st.divider()

        for _, row in df.iterrows():
            partial = row["Estado"] == "PAGO PARCIAL"
            color = "#d00000" if partial else "inherit"
            weight = "700" if partial else "400"
            c1, c2, c3, c4, c5 = st.columns([1.5, 2.5, 1.5, 1.2, 2.3])
            c1.markdown(f'<span style="color:{color};font-weight:{weight}">{row["Pedido"]}</span>', unsafe_allow_html=True)
            c2.markdown(f'<span style="color:{color};font-weight:{weight}">{row["Cliente"]}</span>', unsafe_allow_html=True)
            c3.markdown(f'<span style="color:{color};font-weight:{weight}">{row["País"]}</span>', unsafe_allow_html=True)
            c4.markdown(f'<span style="color:{color};font-weight:{weight}">{row["Fecha"]}</span>', unsafe_allow_html=True)
            new_status = c5.selectbox(
                "Estado", status_options,
                index=status_options.index(row["Estado"]),
                key=f"shipping_status_{int(row['id'])}",
                label_visibility="collapsed"
            )
            if new_status != row["Estado"]:
                conn.execute("UPDATE orders SET status=? WHERE id=?", (new_status, int(row["id"])))
                conn.commit()
                st.rerun()

# ---------------- CLIENTS ----------------
elif page == "Clientes":
    st.title("👥 Clientes")

    tab1, tab2 = st.tabs(["Clientes", "Crear cliente"])

    with tab2:
        st.subheader("Crear cliente")
        with st.form("create_client_form", clear_on_submit=True):
            name = st.text_input("Nombre / Empresa *")
            country = st.text_input("País")
            city = st.text_input("Ciudad")
            address = st.text_input("Dirección")
            postal_code = st.text_input("Código postal")
            email = st.text_input("Email")
            phone = st.text_input("Teléfono")
            notes = st.text_area("Notas")
            create_client = st.form_submit_button("Crear cliente", type="primary")

        if create_client:
            if not name.strip():
                st.error("El nombre / empresa es obligatorio.")
            else:
                conn.execute("""
                    INSERT INTO clients(
                        name,country,city,address,postal_code,email,phone,notes,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    name.strip(), country.strip(), city.strip(), address.strip(),
                    postal_code.strip(), email.strip(), phone.strip(), notes,
                    datetime.now().isoformat(timespec="seconds")
                ))
                conn.commit()
                st.success("Cliente creado correctamente.")
                st.rerun()

    with tab1:
        st.subheader("Listado de clientes")

        clients_df = pd.read_sql_query("""
            SELECT id AS ID, name AS Cliente, country AS País, city AS Ciudad,
                   address AS Dirección, postal_code AS "Código postal",
                   email AS Email, phone AS Teléfono
            FROM clients
            ORDER BY name
        """, conn)

        if clients_df.empty:
            st.info("Todavía no hay clientes registrados.")
        else:
            client_search = st.text_input(
                "🔎 Buscar cliente",
                placeholder="Nombre, empresa, país, ciudad, email o teléfono..."
            )

            filtered_clients = clients_df.copy()
            if client_search.strip():
                q = client_search.strip()
                searchable = ["Cliente","País","Ciudad","Email","Teléfono"]
                mask = False
                for col in searchable:
                    mask = mask | filtered_clients[col].fillna("").astype(str).str.contains(
                        q, case=False, na=False
                    )
                filtered_clients = filtered_clients[mask].copy()

            st.dataframe(
                filtered_clients.drop(columns=["ID"]),
                use_container_width=True,
                hide_index=True
            )

            if filtered_clients.empty:
                st.info("No se encontraron clientes con esa búsqueda.")
            else:
                st.markdown("### Abrir ficha del cliente")
                selected_id = st.selectbox(
                    "Cliente",
                    filtered_clients["ID"].tolist(),
                    format_func=lambda cid: (
                        f"{filtered_clients.loc[filtered_clients['ID']==cid,'Cliente'].iloc[0]}"
                        f" — {filtered_clients.loc[filtered_clients['ID']==cid,'País'].iloc[0] or 'Sin país'}"
                    )
                )

                client = conn.execute("""
                    SELECT id,name,country,city,address,postal_code,email,phone,notes,created_at
                    FROM clients WHERE id=?
                """, (int(selected_id),)).fetchone()

                if client:
                    (
                        cid, cname, ccountry, ccity, caddress, cpostal,
                        cemail, cphone, cnotes, ccreated
                    ) = client

                    stats = conn.execute("""
                        SELECT COUNT(*),
                               COALESCE(SUM(products_sale_cop + shipping_sale_cop),0)
                        FROM orders WHERE client_id=?
                    """, (cid,)).fetchone()

                    m1, m2 = st.columns(2)
                    m1.metric("Pedidos realizados", int(stats[0] or 0))
                    m2.metric("Total comprado", fmt_cop(float(stats[1] or 0)))

                    st.markdown("#### Historial de pedidos")
                    history = pd.read_sql_query("""
                        SELECT order_number AS Pedido, order_date AS Fecha,
                               country AS País,
                               (products_sale_cop + shipping_sale_cop) AS "Total COP",
                               status AS Estado
                        FROM orders
                        WHERE client_id=?
                        ORDER BY order_date DESC, id DESC
                    """, conn, params=(cid,))
                    if history.empty:
                        st.caption("Este cliente todavía no tiene pedidos.")
                    else:
                        st.dataframe(history, use_container_width=True, hide_index=True)

                    st.markdown("#### Editar información")
                    with st.form(f"edit_client_{cid}"):
                        e_name = st.text_input("Nombre / Empresa *", value=cname or "")
                        e_country = st.text_input("País", value=ccountry or "")
                        e_city = st.text_input("Ciudad", value=ccity or "")
                        e_address = st.text_input("Dirección", value=caddress or "")
                        e_postal = st.text_input("Código postal", value=cpostal or "")
                        e_email = st.text_input("Email", value=cemail or "")
                        e_phone = st.text_input("Teléfono", value=cphone or "")
                        e_notes = st.text_area("Notas", value=cnotes or "")
                        save_changes = st.form_submit_button("Guardar cambios", type="primary")

                    if save_changes:
                        if not e_name.strip():
                            st.error("El nombre / empresa es obligatorio.")
                        else:
                            conn.execute("""
                                UPDATE clients
                                SET name=?, country=?, city=?, address=?, postal_code=?,
                                    email=?, phone=?, notes=?
                                WHERE id=?
                            """, (
                                e_name.strip(), e_country.strip(), e_city.strip(),
                                e_address.strip(), e_postal.strip(), e_email.strip(),
                                e_phone.strip(), e_notes, cid
                            ))
                            conn.commit()
                            st.success("Cliente actualizado.")
                            st.rerun()

                    st.markdown("#### Eliminar cliente")
                    order_count = int(stats[0] or 0)
                    if order_count > 0:
                        st.warning(
                            "Este cliente tiene pedidos asociados y no puede eliminarse, "
                            "porque hacerlo rompería el historial contable."
                        )
                    else:
                        confirm = st.checkbox(
                            "Confirmo que deseo eliminar permanentemente este cliente",
                            key=f"delete_confirm_{cid}"
                        )
                        if st.button(
                            "🗑️ Eliminar cliente",
                            disabled=not confirm,
                            key=f"delete_client_{cid}"
                        ):
                            conn.execute("DELETE FROM clients WHERE id=?", (cid,))
                            conn.commit()
                            st.success("Cliente eliminado.")
                            st.rerun()

# ---------------- EXPENSES ----------------
elif page == "Gastos":
    st.title("💸 Gastos")

    categories = ["Nómina","Arriendo","Publicidad","Empaques","Transporte","Software",
                  "Comisiones","Servicios","Papelería","Compras","Impuestos","Otros"]
    methods = ["Transferencia","Tarjeta","Efectivo","Nequi","PayPal","Wise","Otro"]

    with st.form("expense"):
        edate = st.date_input("Fecha", value=date.today())
        category = st.selectbox("Categoría", categories)
        description = st.text_input("Descripción")
        amount = st.number_input("Valor", min_value=0.0, step=10.0)
        currency = st.selectbox("Divisa", ["COP","EUR","USD"])
        default_rate = {"EUR":setting("eur_cop"),"USD":setting("usd_cop"),"COP":1}[currency]
        rate = st.number_input("Tasa utilizada", min_value=0.0001, value=float(default_rate), step=1.0)
        method = st.selectbox("Método de pago", methods)
        supplier = st.text_input("Proveedor")
        notes = st.text_area("Notas")
        save_exp = st.form_submit_button("Registrar gasto", type="primary")

    if save_exp:
        if not description.strip():
            st.error("La descripción es obligatoria.")
        else:
            cop = amount * rate
            conn.execute("""
                INSERT INTO expenses(expense_date,category,description,amount,currency,rate,cop_amount,method,supplier,notes,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (str(edate),category,description.strip(),amount,currency,rate,cop,method,supplier.strip(),notes,datetime.now().isoformat(timespec="seconds")))
            conn.commit()
            st.success(f"Gasto registrado: {fmt_cop(cop)}")

    df = pd.read_sql_query("""
        SELECT expense_date AS Fecha, category AS Categoría, description AS Descripción,
               amount AS Valor, currency AS Divisa, rate AS Tasa,
               cop_amount AS COP, method AS Método, supplier AS Proveedor
        FROM expenses ORDER BY expense_date DESC, id DESC
    """, conn)
    st.subheader("Gastos registrados")
    st.dataframe(df, use_container_width=True, hide_index=True)

# ---------------- REPORTS ----------------
elif page == "Reportes":
    st.title("📊 Reportes")

    start = st.date_input("Desde", value=date(date.today().year, 1, 1))
    end = st.date_input("Hasta", value=date.today())

    sales = pd.read_sql_query("""
        SELECT o.order_number AS Pedido, c.name AS Cliente, o.country AS País,
               o.order_date AS Fecha, o.status AS Estado,
               o.products_amount_original AS Productos_Original,
               o.products_currency AS Divisa_Productos,
               o.products_sale_cop AS Productos_COP,
               o.shipping_charged_original AS Envío_Cobrado_Original,
               o.shipping_currency AS Divisa_Envío,
               o.shipping_sale_cop AS Envío_Cobrado_COP,
               o.product_cost_cop AS Costo_Productos_COP,
               o.real_shipping_original AS Envío_Real_Original,
               o.real_shipping_currency AS Divisa_Envío_Real,
               o.real_shipping_cop AS Envío_Real_COP,
               (o.products_sale_cop + o.shipping_sale_cop) AS Venta_Total_COP,
               (o.products_sale_cop - o.product_cost_cop) AS Utilidad_Productos_COP,
               CASE
                   WHEN o.real_shipping_cop IS NULL THEN NULL
                   ELSE (o.products_sale_cop - o.product_cost_cop
                         + o.shipping_sale_cop - o.real_shipping_cop)
               END AS Utilidad_Real_Pedido_COP
        FROM orders o JOIN clients c ON c.id=o.client_id
        WHERE date(o.order_date) BETWEEN date(?) AND date(?)
        ORDER BY o.order_date
    """, conn, params=(str(start), str(end)))

    expenses = pd.read_sql_query("""
        SELECT expense_date AS Fecha, category AS Categoría, description AS Descripción,
               amount AS Valor, currency AS Divisa, rate AS Tasa, cop_amount AS COP,
               method AS Método, supplier AS Proveedor
        FROM expenses
        WHERE date(expense_date) BETWEEN date(?) AND date(?)
        ORDER BY expense_date
    """, conn, params=(str(start), str(end)))

    payments = pd.read_sql_query("""
        SELECT p.payment_date AS Fecha, o.order_number AS Pedido, c.name AS Cliente,
               p.amount AS Monto, p.currency AS Divisa, p.rate AS Tasa,
               p.cop_amount AS COP, p.method AS Método, p.reference AS Referencia
        FROM payments p
        JOIN orders o ON o.id=p.order_id
        JOIN clients c ON c.id=o.client_id
        WHERE date(p.payment_date) BETWEEN date(?) AND date(?)
        ORDER BY p.payment_date
    """, conn, params=(str(start), str(end)))

    st.subheader("Ventas")
    st.metric("Pedidos", len(sales))
    st.metric("Ventas", fmt_cop(sales["Venta_Total_COP"].sum() if not sales.empty else 0))
    if not sales.empty:
        st.dataframe(sales, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Exportar ventas a Excel", excel_bytes(sales), "ventas_raiz_latina.xlsx")

    st.subheader("Pagos")
    if not payments.empty:
        st.dataframe(payments, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Exportar pagos a Excel", excel_bytes(payments), "pagos_raiz_latina.xlsx")

    st.subheader("Gastos")
    if not expenses.empty:
        st.dataframe(expenses, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Exportar gastos a Excel", excel_bytes(expenses), "gastos_raiz_latina.xlsx")

    st.subheader("Resumen del período")
    sales_total = sales["Venta_Total_COP"].sum() if not sales.empty else 0
    product_cost = sales["Costo_Productos_COP"].sum() if not sales.empty else 0
    shipping_real = sales["Envío_Real_COP"].dropna().sum() if not sales.empty else 0
    pending_shipping = int(sales["Envío_Real_COP"].isna().sum()) if not sales.empty else 0
    shipping_charged = sales["Envío_Cobrado_COP"].sum() if not sales.empty else 0
    exp_total = expenses["COP"].sum() if not expenses.empty else 0

    # Estimated result: use the amount charged for shipping as provisional shipping cost.
    estimated_result = sales_total - product_cost - shipping_charged - exp_total

    # Available result: actual shipping where entered, charged shipping where still pending.
    if not sales.empty:
        available_shipping_cost = (
            sales["Envío_Real_COP"].fillna(sales["Envío_Cobrado_COP"]).sum()
        )
    else:
        available_shipping_cost = 0
    available_result = sales_total - product_cost - available_shipping_cost - exp_total

    r1,r2,r3,r4 = st.columns(4)
    r1.metric("Ventas", fmt_cop(sales_total))
    r2.metric("Costo productos", fmt_cop(product_cost))
    r3.metric("Gastos", fmt_cop(exp_total))
    r4.metric("Resultado estimado", fmt_cop(estimated_result))
    if pending_shipping:
        st.info(
            f"{pending_shipping} pedido(s) aún no tienen costo real de envío. "
            f"Resultado usando el mejor costo disponible: {fmt_cop(available_result)}."
        )
    else:
        st.success(f"Todos los envíos tienen costo real. Resultado: {fmt_cop(available_result)}.")

# ---------------- SETTINGS ----------------
elif page == "Configuración":
    st.title("⚙️ Configuración")

    ref = get_reference_rates()
    st.subheader("Tasas de cambio")
    st.caption("Las tasas automáticas son de referencia. Las tasas configuradas y las tasas reales de cada operación se guardan históricamente.")

    eur = st.number_input("Tasa EUR → COP de Raíz Latina", min_value=0.0001, value=setting("eur_cop"), step=1.0)
    usd = st.number_input("Tasa USD → COP de Raíz Latina", min_value=0.0001, value=setting("usd_cop"), step=1.0)

    a,b = st.columns(2)
    with a:
        st.metric("EUR automático de referencia", fmt_cop(ref["EUR"]) if ref["EUR"] else "No disponible")
    with b:
        st.metric("USD automático de referencia", fmt_cop(ref["USD"]) if ref["USD"] else "No disponible")

    margin = st.number_input(
        "Margen objetivo sobre el precio de venta de productos (%)",
        min_value=0.0, max_value=100.0, value=setting("product_margin"), step=1.0
    )

    st.info(
        "Con 25%, el costo estimado de productos se calcula como 75% del valor de venta. "
        "Ejemplo: venta €1.000 → costo estimado €750 → utilidad de productos €250."
    )

    if st.button("Guardar configuración", type="primary"):
        set_setting("eur_cop", eur)
        set_setting("usd_cop", usd)
        set_setting("product_margin", margin)
        st.success("Configuración guardada.")

    st.divider()
    st.subheader("Respaldo local")
    st.caption("La base de datos se guarda como raiz_latina.db en la carpeta del proyecto. Haz copias periódicas del archivo.")
    if st.button("Cerrar conexión y recargar"):
        conn.close()
        st.rerun()
