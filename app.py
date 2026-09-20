
import streamlit as st
import psycopg2
import pandas as pd
import requests
import os
import math
import re
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

APP_DIR = Path(__file__).resolve().parent
LOGO_PATH = APP_DIR / "logo_raiz_latina.png"

st.set_page_config(page_title="Raíz Latina — Modelo Financiero", page_icon="🌎", layout="wide")
st.markdown("""
<style>
.block-container {max-width: 100%; padding: 1rem 2rem 2rem 1.4rem;}
[data-testid="stSidebar"] {min-width: 245px; max-width: 270px;}
[data-testid="stMetricValue"] {font-size: 1.45rem;}
</style>
""", unsafe_allow_html=True)

# ---------------- DATABASE ----------------

class PGConnection:
    def __init__(self, dsn):
        self.raw = psycopg2.connect(dsn, sslmode="require")
        self.raw.autocommit = False

    @staticmethod
    def _sql(sql):
        return sql.replace("?", "%s")

    def execute(self, sql, params=None):
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), params or ())
        return cur

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def get_dsn():
    try:
        if "database" in st.secrets:
            cfg = st.secrets["database"]
            if isinstance(cfg, str):
                return cfg
            return cfg.get("url") or cfg.get("connection_string")
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass
    return os.getenv("DATABASE_URL")


def get_conn():
    dsn = get_dsn()
    if not dsn:
        st.error("No se encontró la conexión a PostgreSQL. Configura DATABASE_URL en Streamlit Secrets.")
        st.stop()
    return PGConnection(dsn)


def init_db():
    conn = get_conn()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS clients (
        id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, country TEXT, city TEXT, address TEXT,
        postal_code TEXT, email TEXT, phone TEXT, notes TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS orders (
        id BIGSERIAL PRIMARY KEY, order_number TEXT UNIQUE NOT NULL, client_id BIGINT NOT NULL REFERENCES clients(id),
        country TEXT NOT NULL, order_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'POR CONFIRMAR',
        products_amount_original DOUBLE PRECISION NOT NULL, products_currency TEXT NOT NULL,
        shipping_charged_original DOUBLE PRECISION NOT NULL DEFAULT 0, shipping_currency TEXT NOT NULL,
        sale_rate DOUBLE PRECISION NOT NULL, products_sale_cop DOUBLE PRECISION NOT NULL,
        shipping_sale_cop DOUBLE PRECISION NOT NULL, product_cost_cop DOUBLE PRECISION NOT NULL,
        estimated_profit_cop DOUBLE PRECISION NOT NULL, real_shipping_original DOUBLE PRECISION,
        real_shipping_currency TEXT, real_shipping_rate DOUBLE PRECISION, real_shipping_cop DOUBLE PRECISION,
        notes TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS payments (
        id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        payment_date TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL, currency TEXT NOT NULL, rate DOUBLE PRECISION NOT NULL,
        cop_amount DOUBLE PRECISION NOT NULL, method TEXT, reference TEXT, notes TEXT
    );
    CREATE TABLE IF NOT EXISTS expenses (
        id BIGSERIAL PRIMARY KEY, expense_date TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
        amount DOUBLE PRECISION NOT NULL, currency TEXT NOT NULL, rate DOUBLE PRECISION NOT NULL, cop_amount DOUBLE PRECISION NOT NULL,
        method TEXT, supplier TEXT, notes TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS shipment_history (
        id BIGSERIAL PRIMARY KEY,
        order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        previous_status TEXT NOT NULL,
        shipped_at TEXT NOT NULL,
        returned_at TEXT,
        return_notes TEXT
    );
    CREATE TABLE IF NOT EXISTS quotations (
        id BIGSERIAL PRIMARY KEY,
        quotation_number TEXT UNIQUE NOT NULL,
        client_id BIGINT NOT NULL REFERENCES clients(id),
        recipient_name TEXT,
        created_at TEXT NOT NULL,
        valid_until TEXT NOT NULL,
        country TEXT, city TEXT, address TEXT, postal_code TEXT, email TEXT, phone TEXT,
        currency TEXT NOT NULL,
        products_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
        product_discount_type TEXT NOT NULL DEFAULT 'NINGUNO',
        product_discount_value DOUBLE PRECISION NOT NULL DEFAULT 0,
        product_discount_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
        product_net DOUBLE PRECISION NOT NULL DEFAULT 0,
        product_weight DOUBLE PRECISION NOT NULL DEFAULT 0,
        packaging_weight DOUBLE PRECISION NOT NULL DEFAULT 0,
        billed_weight DOUBLE PRECISION NOT NULL DEFAULT 0,
        boxes_count INTEGER NOT NULL DEFAULT 1,
        shipping_base DOUBLE PRECISION NOT NULL DEFAULT 0,
        shipping_discount_type TEXT NOT NULL DEFAULT 'NINGUNO',
        shipping_discount_value DOUBLE PRECISION NOT NULL DEFAULT 0,
        shipping_discount_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
        shipping_net DOUBLE PRECISION NOT NULL DEFAULT 0,
        incoterm TEXT NOT NULL DEFAULT 'DAP',
        tax_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
        tax_per_box DOUBLE PRECISION NOT NULL DEFAULT 35,
        tax_manual BOOLEAN NOT NULL DEFAULT FALSE,
        total_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
        notes TEXT, status TEXT NOT NULL DEFAULT 'PENDIENTE',
        order_id BIGINT, box_details TEXT
    );
    CREATE TABLE IF NOT EXISTS shipping_rates (
        id BIGSERIAL PRIMARY KEY, country TEXT NOT NULL, kg DOUBLE PRECISION NOT NULL,
        rate DOUBLE PRECISION NOT NULL, currency TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(country, kg)
    );
    """)
    # Campos opcionales para DAP/DDP, impuestos y trazabilidad con cotizaciones.
    # No se modifica ni elimina ningún dato financiero existente.
    for sql in [
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS incoterm TEXT DEFAULT 'DAP'",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax_amount_original DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax_currency TEXT",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax_cop DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax_per_box DOUBLE PRECISION DEFAULT 35",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax_manual BOOLEAN DEFAULT FALSE",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS box_count INTEGER DEFAULT 1",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS billed_weight DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS quotation_id BIGINT"
    ]:
        conn.execute(sql)

    defaults = {"eur_cop": "3600", "usd_cop": "3900", "product_margin": "25"}
    for k, v in defaults.items():
        conn.execute("INSERT INTO settings(key,value) VALUES (%s,%s) ON CONFLICT (key) DO NOTHING", (k, v))
    conn.commit()
    return conn

# Schema initialization is additive only: it creates missing tables/columns and never deletes orders.
conn = init_db()

# Carga inicial de las tarifas incluidas en el proyecto. Si Neon ya tiene tarifas, no las reemplaza.
def _seed_bundled_rates():
    try:
        count = conn.execute("SELECT COUNT(*) FROM shipping_rates").fetchone()[0]
        rates_file = APP_DIR / "dicc_envios.xlsx"
        if count == 0 and rates_file.exists():
            df = pd.read_excel(rates_file)
            cols = {str(c).strip().lower(): c for c in df.columns}
            ctry = cols.get("país") or cols.get("pais") or cols.get("country")
            kgcol = cols.get("kg") or cols.get("peso")
            ratecol = cols.get("tarifa") or cols.get("rate") or cols.get("valor")
            if ctry and kgcol and ratecol:
                europe = {"españa","espana","francia","alemania","italia","portugal","países bajos","paises bajos","bélgica","belgica","austria","irlanda","luxemburgo"}
                for _, row in df.iterrows():
                    country = str(row[ctry]).strip()
                    if not country or country.lower() == "nan": continue
                    kg = float(row[kgcol]); rate = float(row[ratecol])
                    cur = "EUR" if country.lower().strip() in europe else "USD"
                    conn.execute("INSERT INTO shipping_rates(country,kg,rate,currency,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(country,kg) DO NOTHING", (country,kg,rate,cur,datetime.now().isoformat(timespec="seconds")))
                conn.commit()
    except Exception:
        conn.rollback()

_seed_bundled_rates()

def read_sql_query(query, conn_obj=None, params=None):
    """Read a query using a short-lived cursor and release the transaction immediately.

    This is important with Streamlit reruns: leaving SELECT transactions open can keep
    PostgreSQL locks alive and make the next rerun appear to freeze while init_db()
    performs its schema checks.
    """
    db = conn_obj.raw if conn_obj is not None else conn.raw
    cur = db.cursor()
    try:
        cur.execute(query.replace("?", "%s"), params or ())
        rows = cur.fetchall() if cur.description else []
        columns = [d[0] for d in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=columns)
    finally:
        cur.close()
        db.commit()


def fetch_one(query, params=None, conn_obj=None):
    """Fetch one row and immediately release the read transaction."""
    db = conn_obj.raw if conn_obj is not None else conn.raw
    cur = db.cursor()
    try:
        cur.execute(query.replace("?", "%s"), params or ())
        return cur.fetchone()
    finally:
        cur.close()
        db.commit()


def setting(key):
    row = fetch_one("SELECT value FROM settings WHERE key=?", (key,))
    return float(row[0]) if row else 0.0

def set_setting(key, value):
    conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))
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

# ---------------- QUOTATIONS HELPERS ----------------
def q_country_currency(country):
    europe = {"españa","espana","francia","alemania","italia","portugal","países bajos","paises bajos","bélgica","belgica","austria","irlanda","luxemburgo"}
    c = re.sub(r"\s+", " ", str(country or "").strip().lower())
    return "EUR" if c in europe else "USD"

def q_money(value, currency):
    return f"{currency} {float(value):,.2f}".replace(",","X").replace(".",",").replace("X",".")

def q_next_number():
    row=fetch_one("SELECT quotation_number FROM quotations ORDER BY id DESC LIMIT 1")
    if not row: return "COT-0001"
    m=re.search(r"(\d+)$",str(row[0]))
    return f"COT-{int(m.group(1))+1:04d}" if m else "COT-0001"

def q_load_rates():
    return read_sql_query("SELECT country,kg,rate,currency FROM shipping_rates ORDER BY country,kg",conn)

def q_rate(country,kg):
    rates=q_load_rates()
    if rates.empty: return None,None
    target=str(country).strip().lower()
    sub=rates[rates["country"].astype(str).str.strip().str.lower()==target].copy()
    if sub.empty: return None,None
    sub["kg"]=pd.to_numeric(sub["kg"],errors="coerce"); sub["rate"]=pd.to_numeric(sub["rate"],errors="coerce")
    sub=sub.dropna(subset=["kg","rate"]).sort_values("kg")
    cand=sub[sub["kg"]>=float(kg)-1e-9]
    if cand.empty:return None,None
    r=cand.iloc[0]; return float(r["rate"]),float(r["kg"])

def q_split_boxes(product_weight):
    w=max(float(product_weight),0)
    if w<=0:return []
    n=max(1,math.ceil(w/14.0-1e-10))
    base=w/n
    boxes=[base]*n
    boxes[-1]+=w-sum(boxes)
    return [round(x,3) for x in boxes]

def q_billed(gross):
    return math.ceil((float(gross)-1e-9)*2)/2

def q_calculate_shipping(country,product_weight):
    boxes=q_split_boxes(product_weight)
    if not boxes:return None,"Ingresa un peso de productos mayor que 0."
    currency=q_country_currency(country); details=[]; total=0
    for i,pkg in enumerate(boxes,1):
        gross=pkg+1
        billed=q_billed(gross)
        rate,tariff=q_rate(country,billed)
        if rate is None:return None,f"No existe una tarifa para {country} en {billed:.1f} kg. Revisa el Excel de tarifas."
        details.append({"box":i,"product_kg":pkg,"gross_kg":gross,"billed_kg":billed,"tariff_kg":tariff,"rate":rate})
        total+=rate
    return {"boxes":len(boxes),"details":details,"total":total,"currency":currency,"billed_weight":sum(x["billed_kg"] for x in details)},None

def q_discount(base,kind,value):
    if kind=="NINGUNO":return 0.0
    if kind=="%":return max(0,min(base,base*float(value)/100))
    return max(0,min(base,float(value)))

def q_client(client_id):
    r=fetch_one("SELECT id,name,country,city,address,postal_code,email,phone FROM clients WHERE id=?",(int(client_id),))
    if not r:return None
    keys=["id","name","country","city","address","postal_code","email","phone"]
    return dict(zip(keys,r))

def q_pdf(q,client):
    """Generate a clean, white-background quotation PDF with the color brand logo."""
    if not REPORTLAB_OK:
        return None
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=12*mm, bottomMargin=12*mm
    )
    styles = getSampleStyleSheet()
    green = colors.HexColor("#1B4D3E")
    dark = colors.HexColor("#252A27")
    gray = colors.HexColor("#6B716D")
    line = colors.HexColor("#D9DEDA")
    pale = colors.HexColor("#F4F7F5")

    body = ParagraphStyle("RLBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=11, textColor=dark)
    small = ParagraphStyle("RLSmall", parent=body, fontSize=7.8, leading=9.5, textColor=gray)
    section = ParagraphStyle("RLSection", parent=body, fontName="Helvetica-Bold", fontSize=9.5, leading=11, textColor=green)
    title = ParagraphStyle("RLTitle", parent=body, fontName="Helvetica-Bold", fontSize=20, leading=22, textColor=green, alignment=2)
    total_style = ParagraphStyle("RLTotal", parent=body, fontName="Helvetica-Bold", fontSize=12, textColor=green, alignment=2)

    story = []
    logo = Image(str(LOGO_PATH), width=55*mm, height=28*mm, kind="proportional") if LOGO_PATH.exists() else Paragraph("<b>Raíz Latina</b>", section)
    meta = Paragraph(
        f"<b>COTIZACIÓN</b><br/><font size='12'><b>{q['quotation_number']}</b></font><br/>"
        f"Fecha: {q['created_at']}<br/>Válida hasta: {q['valid_until']}", body
    )
    header = Table([[logo, meta]], colWidths=[100*mm, 70*mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (1,0), (1,0), "RIGHT"),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3*mm),
        ("LINEBELOW", (0,0), (-1,-1), 0.8, green),
    ]))
    story += [header, Spacer(1, 5*mm)]

    client_name = str(client.get("name") or q.get("recipient_name") or "").strip()
    address_parts = [client.get("address"), client.get("city"), client.get("postal_code"), client.get("country")]
    address = "<br/>".join(str(x).strip() for x in address_parts if str(x or "").strip())
    client_text = f"<b>{client_name}</b>"
    if address: client_text += f"<br/>{address}"
    if client.get("phone"): client_text += f"<br/>{client['phone']}"
    if client.get("email"): client_text += f"<br/>{client['email']}"

    seller_text = "<b>RAÍZ LATINA</b><br/>Mayoristas<br/>Medellín, Colombia"
    info = Table([
        [Paragraph("DATOS DEL VENDEDOR", section), Paragraph("DATOS DEL CLIENTE", section)],
        [Paragraph(seller_text, body), Paragraph(client_text, body)]
    ], colWidths=[85*mm, 85*mm])
    info.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), pale),
        ("BOX", (0,0), (-1,-1), 0.5, line),
        ("INNERGRID", (0,0), (-1,-1), 0.35, line),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 4*mm),
        ("RIGHTPADDING", (0,0), (-1,-1), 4*mm),
        ("TOPPADDING", (0,0), (-1,-1), 3*mm),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3*mm),
    ]))
    story += [info, Spacer(1, 5*mm)]

    rows = [["CONCEPTO", f"VALOR ({q['currency']})"]]
    rows.append(["Productos", q_money(q["products_amount"], q["currency"])])
    if q["product_discount_amount"] > 0:
        label = "Descuento productos"
        if q["product_discount_type"] == "%": label += f" ({q['product_discount_value']:.0f}%)"
        rows.append([label, "- " + q_money(q["product_discount_amount"], q["currency"])])
    rows.append(["Productos netos", q_money(q["product_net"], q["currency"])])
    rows.append(["Envío", q_money(q["shipping_base"], q["currency"])])
    if q["shipping_discount_amount"] > 0:
        label = "Descuento envío"
        if q["shipping_discount_type"] == "%": label += f" ({q['shipping_discount_value']:.0f}%)"
        rows.append([label, "- " + q_money(q["shipping_discount_amount"], q["currency"])])
    rows.append(["Envío neto", q_money(q["shipping_net"], q["currency"])])
    if q["tax_amount"] > 0:
        rows.append(["Impuestos", q_money(q["tax_amount"], q["currency"])])
    rows.append(["TOTAL", q_money(q["total_amount"], q["currency"])])

    quote_table = Table(rows, colWidths=[112*mm, 58*mm], repeatRows=1)
    quote_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), green),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("GRID", (0,0), (-1,-1), 0.35, line),
        ("ALIGN", (1,1), (1,-1), "RIGHT"),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#EAF2ED")),
        ("TEXTCOLOR", (0,-1), (-1,-1), green),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
    ]))
    story += [quote_table, Spacer(1, 5*mm)]

    shipping_info = (
        f"<b>Destino:</b> {q['country']} &nbsp;&nbsp; "
        f"<b>Incoterm:</b> {q['incoterm']} &nbsp;&nbsp; "
        f"<b>Cajas:</b> {q['boxes_count']} &nbsp;&nbsp; "
        f"<b>Peso facturado:</b> {q['billed_weight']:.1f} kg"
    )
    ship = Table([[Paragraph(shipping_info, body)]], colWidths=[170*mm])
    ship.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), pale),
        ("BOX", (0,0), (-1,-1), 0.5, line),
        ("LEFTPADDING", (0,0), (-1,-1), 4*mm),
        ("RIGHTPADDING", (0,0), (-1,-1), 4*mm),
        ("TOPPADDING", (0,0), (-1,-1), 3*mm),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3*mm),
    ]))
    story += [ship, Spacer(1, 5*mm)]

    cond = (
        "<b>CONDICIONES COMERCIALES</b><br/>"
        "• Esta cotización es válida por 15 días calendario desde su fecha de creación.<br/>"
        "• Los impuestos indicados corresponden únicamente a los valores expresamente incluidos en esta cotización.<br/>"
        "• En DDP, los impuestos incluidos son los indicados en la cotización; en DAP, los impuestos de destino no están incluidos salvo indicación expresa.<br/>"
        "• Los tiempos de entrega y costos finales pueden variar según las condiciones del país de destino."
    )
    conditions = Table([[Paragraph(cond, small)]], colWidths=[170*mm])
    conditions.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.5, line),
        ("LEFTPADDING", (0,0), (-1,-1), 4*mm),
        ("RIGHTPADDING", (0,0), (-1,-1), 4*mm),
        ("TOPPADDING", (0,0), (-1,-1), 3*mm),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3*mm),
    ]))
    story += [conditions, Spacer(1, 4*mm), Paragraph("Raíz Latina · Mayoristas · Medellín, Colombia", small)]
    doc.build(story)
    return buf.getvalue()

def q_import_rates(upload):
    name=upload.name.lower(); raw=upload.getvalue()
    if name.endswith((".xlsx",".xls")): df=pd.read_excel(BytesIO(raw))
    else:
        df=None
        for sep in [";",",","\\t"]:
            try:
                d=pd.read_csv(BytesIO(raw),sep=sep,encoding="utf-8-sig")
                if len(d.columns)>=3: df=d; break
            except Exception: pass
    if df is None or len(df.columns)<3: raise ValueError("No pude leer el archivo de tarifas.")
    cols={str(c).strip().lower():c for c in df.columns}
    def pick(names):
        for n in names:
            if n in cols:return cols[n]
        return None
    ctry=pick(["país","pais","country"]); kg=pick(["kg","peso","peso kg"]); rate=pick(["tarifa","rate","precio","valor"])
    if not all([ctry,kg,rate]):raise ValueError("El archivo debe contener País, Kg y Tarifa.")
    now=datetime.now().isoformat(timespec="seconds"); n=0
    for _,r in df.iterrows():
        country=str(r[ctry]).strip()
        if not country or country.lower()=="nan":continue
        k=float(str(r[kg]).replace(",",".")); v=float(str(r[rate]).replace(",",".")); cur=q_country_currency(country)
        conn.execute("""INSERT INTO shipping_rates(country,kg,rate,currency,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(country,kg) DO UPDATE SET rate=EXCLUDED.rate,currency=EXCLUDED.currency,updated_at=EXCLUDED.updated_at""",(country,k,v,cur,now)); n+=1
    conn.commit(); return n

def render_quotations():
    st.title("🧾 Cotizaciones")
    tab1, tab2, tab3 = st.tabs(["Nueva cotización", "Listado", "Tarifas de envío"])

    # ---------------- NEW QUOTATION ----------------
    with tab1:
        clients = read_sql_query("SELECT id,name,country FROM clients ORDER BY name", conn)
        if clients.empty:
            st.warning("Primero crea al menos un cliente en Clientes.")
        else:
            client_search = st.text_input(
                "🔎 Buscar cliente",
                placeholder="Escribe nombre, empresa o país...",
                key="new_quote_client_search"
            )
            filtered_clients = clients.copy()
            if client_search.strip():
                q = client_search.strip()
                mask = (
                    filtered_clients["name"].fillna("").astype(str).str.contains(q, case=False, na=False) |
                    filtered_clients["country"].fillna("").astype(str).str.contains(q, case=False, na=False)
                )
                filtered_clients = filtered_clients[mask].copy()

            if filtered_clients.empty:
                st.info("No se encontraron clientes con esa búsqueda.")
            else:
                ids = filtered_clients["id"].tolist()
                sel = st.selectbox(
                    "Cliente",
                    ids,
                    format_func=lambda x: f"{filtered_clients.loc[filtered_clients.id==x,'name'].iloc[0]} — {filtered_clients.loc[filtered_clients.id==x,'country'].iloc[0] or 'Sin país'}",
                    key="new_quote_client_select"
                )
                c = q_client(sel)
                a, b = st.columns(2)
                with a:
                    name = st.text_input("Nombre / Empresa", value=c["name"] or "")
                    email = st.text_input("Correo", value=c["email"] or "")
                    phone = st.text_input("Teléfono", value=c["phone"] or "")
                    country = st.text_input("País", value=c["country"] or "")
                with b:
                    city = st.text_input("Ciudad", value=c["city"] or "")
                    address = st.text_area("Dirección", value=c["address"] or "")
                    postal = st.text_input("Código postal", value=c["postal_code"] or "")

                x, y, z = st.columns(3)
                with x:
                    cur_default = q_country_currency(country)
                    currency = st.selectbox("Divisa", ["EUR", "USD"], index=0 if cur_default == "EUR" else 1)
                    products = st.number_input("Valor de productos", min_value=0.0, step=10.0)
                    weight = st.number_input("Peso de productos (kg)", min_value=0.0, step=0.5)
                with y:
                    pdt = st.selectbox("Descuento productos", ["NINGUNO", "%", "VALOR"])
                    pdv = st.number_input("Descuento productos", min_value=0.0, step=1.0, disabled=pdt == "NINGUNO")
                    inc = st.selectbox("Término de negociación", ["DAP", "DDP"])
                with z:
                    sdt = st.selectbox("Descuento envío", ["NINGUNO", "%", "VALOR"])
                    sdv = st.number_input("Descuento envío", min_value=0.0, step=1.0, disabled=sdt == "NINGUNO")
                    st.caption("En DDP: 35 por caja por defecto; puede modificarse.")

                if st.button("🧮 Calcular cotización", type="primary", key="calculate_new_quote"):
                    calc, err = q_calculate_shipping(country, weight)
                    if err:
                        st.error(err)
                    elif products <= 0:
                        st.error("Ingresa el valor de productos.")
                    else:
                        pdisc = q_discount(products, pdt, pdv)
                        sdisc = q_discount(calc["total"], sdt, sdv)
                        pnet = products - pdisc
                        snet = calc["total"] - sdisc
                        tax = calc["boxes"] * 35 if inc == "DDP" else 0
                        total = pnet + snet + tax
                        st.session_state["quote_draft"] = {
                            "client_id": sel, "name": name, "email": email, "phone": phone,
                            "country": country, "city": city, "address": address, "postal_code": postal,
                            "currency": currency, "products_amount": products,
                            "product_discount_type": pdt, "product_discount_value": pdv,
                            "product_discount_amount": pdisc, "product_net": pnet,
                            "product_weight": weight, "billed_weight": calc["billed_weight"],
                            "boxes_count": calc["boxes"], "shipping_base": calc["total"],
                            "shipping_discount_type": sdt, "shipping_discount_value": sdv,
                            "shipping_discount_amount": sdisc, "shipping_net": snet,
                            "incoterm": inc, "tax_amount": tax, "tax_per_box": 35.0,
                            "tax_manual": False, "total_amount": total,
                            "box_details": calc["details"], "notes": ""
                        }
                        st.rerun()

                qd = st.session_state.get("quote_draft")
                if qd:
                    st.divider()
                    st.subheader("Resultado de la cotización")
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Cajas", qd["boxes_count"])
                    m2.metric("Peso facturado", f"{qd['billed_weight']:.1f} kg")
                    m3.metric("Envío", q_money(qd["shipping_net"], qd["currency"]))
                    m4.metric("Total", q_money(qd["total_amount"], qd["currency"]))
                    with st.expander("Información interna de cajas"):
                        for d in qd["box_details"]:
                            st.write(f"Caja {d['box']}: {d['product_kg']:.2f} kg productos + 1 kg embalaje = {d['billed_kg']:.1f} kg facturados · tarifa {q_money(d['rate'], qd['currency'])}")
                    if qd["incoterm"] == "DDP":
                        manual = st.checkbox("Modificar manualmente impuestos", key="new_tax_manual")
                        tax = st.number_input("Impuestos totales", min_value=0.0, value=float(qd["tax_amount"]), step=1.0, key="new_tax_value") if manual else qd["tax_amount"]
                        st.caption(f"Impuesto: {qd['boxes_count']} × {q_money(qd['tax_per_box'], qd['currency'])} = {q_money(tax, qd['currency'])}" if not manual else "Impuesto manual")
                        qd["tax_amount"] = tax
                        qd["tax_manual"] = manual
                        qd["total_amount"] = qd["product_net"] + qd["shipping_net"] + tax
                    qd["notes"] = st.text_area("Observaciones", value=qd.get("notes", ""), key="new_quote_notes")
                    if st.button("💾 Guardar cotización", type="primary", key="save_new_quote"):
                        num = q_next_number(); today = date.today(); valid = today + timedelta(days=15)
                        conn.execute("""INSERT INTO quotations(
                            quotation_number,client_id,recipient_name,created_at,valid_until,country,city,address,postal_code,email,phone,currency,
                            products_amount,product_discount_type,product_discount_value,product_discount_amount,product_net,product_weight,packaging_weight,
                            billed_weight,boxes_count,shipping_base,shipping_discount_type,shipping_discount_value,shipping_discount_amount,shipping_net,
                            incoterm,tax_amount,tax_per_box,tax_manual,total_amount,notes,status,box_details
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                            num, sel, qd["name"], str(today), str(valid), qd["country"], qd["city"], qd["address"], qd["postal_code"],
                            qd["email"], qd["phone"], qd["currency"], qd["products_amount"], qd["product_discount_type"],
                            qd["product_discount_value"], qd["product_discount_amount"], qd["product_net"], qd["product_weight"],
                            1.0, qd["billed_weight"], qd["boxes_count"], qd["shipping_base"], qd["shipping_discount_type"],
                            qd["shipping_discount_value"], qd["shipping_discount_amount"], qd["shipping_net"], qd["incoterm"],
                            qd["tax_amount"], qd["tax_per_box"], qd["tax_manual"], qd["total_amount"], qd["notes"], "PENDIENTE", str(qd["box_details"])
                        ))
                        conn.commit()
                        st.session_state.pop("quote_draft", None)
                        st.success(f"{num} guardada. Puedes verla en Listado.")
                        st.rerun()

    # ---------------- QUOTATION LIST ----------------
    with tab2:
        df = read_sql_query("""SELECT q.id,q.quotation_number AS "Cotización",q.created_at AS "Fecha",c.name AS "Cliente",
            q.country AS "País",q.total_amount AS "Total",q.currency AS "Divisa",q.status AS "Estado"
            FROM quotations q JOIN clients c ON c.id=q.client_id ORDER BY q.id DESC""", conn)
        if df.empty:
            st.info("Todavía no hay cotizaciones.")
        else:
            action_df = df.drop(columns=["id"]).copy()
            action_df["Acción"] = "—"
            edited_actions = st.data_editor(
                action_df,
                use_container_width=True,
                hide_index=True,
                key="quotation_action_table",
                disabled=[c for c in action_df.columns if c != "Acción"],
                column_config={
                    "Acción": st.column_config.SelectboxColumn(
                        "Acción",
                        options=["—", "Descargar PDF", "Editar"],
                        required=True,
                        width="medium"
                    ),
                    "Total": st.column_config.NumberColumn("Total", format="%.2f")
                }
            )
            selected_rows = edited_actions[edited_actions["Acción"] != "—"]
            if not selected_rows.empty:
                selected_index = selected_rows.index[0]
                selected_action = selected_rows.loc[selected_index, "Acción"]
                qid = int(df.iloc[int(selected_index)]["id"])
            else:
                qid = None
                selected_action = None

            if qid is not None:
                r = fetch_one("""SELECT id,quotation_number,client_id,recipient_name,created_at,valid_until,country,city,address,postal_code,email,phone,currency,
                    products_amount,product_discount_type,product_discount_value,product_discount_amount,product_net,product_weight,packaging_weight,billed_weight,
                    boxes_count,shipping_base,shipping_discount_type,shipping_discount_value,shipping_discount_amount,shipping_net,incoterm,tax_amount,tax_per_box,
                    tax_manual,total_amount,notes,status,order_id,box_details FROM quotations WHERE id=?""", (qid,))
                if r:
                    keys = ["id","quotation_number","client_id","recipient_name","created_at","valid_until","country","city","address","postal_code","email","phone","currency",
                            "products_amount","product_discount_type","product_discount_value","product_discount_amount","product_net","product_weight","packaging_weight","billed_weight","boxes_count",
                            "shipping_base","shipping_discount_type","shipping_discount_value","shipping_discount_amount","shipping_net","incoterm","tax_amount","tax_per_box","tax_manual","total_amount","notes","status","order_id","box_details"]
                    q = dict(zip(keys, r))
                    client = q_client(q["client_id"])
                    if client:
                        client = client.copy()
                        client.update({"name": q.get("recipient_name") or client.get("name"), "country": q.get("country"), "city": q.get("city"), "address": q.get("address"), "postal_code": q.get("postal_code"), "email": q.get("email"), "phone": q.get("phone")})

                    st.caption(f"Estado: {q['status']} · Válida hasta: {q['valid_until']}")
                    if selected_action == "Descargar PDF":
                        if REPORTLAB_OK and client:
                            safe = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚáéíóúÑñ _-]+", "", client["name"] or "cliente").strip().replace(" ", "_")
                            pdf = q_pdf(q, client)
                            st.download_button("📄 Descargar PDF", pdf, f"Cotizacion {q['quotation_number']} - {safe}.pdf", "application/pdf", key=f"pdf_action_{qid}")
                        else:
                            st.error("No se pudo preparar el PDF.")

                    elif selected_action == "Editar":
                        st.divider(); st.subheader(f"Editar {q['quotation_number']}")
                        editable = q["status"] != "CONFIRMADA"
                        if not editable:
                            st.info("Esta cotización ya fue confirmada y convertida en pedido. No se modifica desde aquí.")
                        ea, eb = st.columns(2)
                        with ea:
                            ename = st.text_input("Nombre / Empresa", value=q["recipient_name"] or client["name"], key=f"ename_{qid}")
                            ecountry = st.text_input("País", value=q["country"], key=f"ecountry_{qid}")
                            ecity = st.text_input("Ciudad", value=q["city"] or "", key=f"ecity_{qid}")
                            eaddress = st.text_area("Dirección", value=q["address"] or "", key=f"eaddress_{qid}")
                        with eb:
                            eemail = st.text_input("Correo", value=q["email"] or "", key=f"eemail_{qid}")
                            ephone = st.text_input("Teléfono", value=q["phone"] or "", key=f"ephone_{qid}")
                            epostal = st.text_input("Código postal", value=q["postal_code"] or "", key=f"epostal_{qid}")
                        ec1, ec2, ec3 = st.columns(3)
                        with ec1:
                            ecurrency = st.selectbox("Divisa", ["EUR", "USD"], index=0 if q["currency"] == "EUR" else 1, key=f"ecur_{qid}")
                            eproducts = st.number_input("Valor productos", min_value=0.0, value=float(q["products_amount"]), step=10.0, key=f"eprod_{qid}")
                            epweight = st.number_input("Peso productos (kg)", min_value=0.0, value=float(q["product_weight"]), step=0.5, key=f"epw_{qid}")
                        with ec2:
                            epdt = st.selectbox("Descuento productos", ["NINGUNO", "%", "VALOR"], index=["NINGUNO", "%", "VALOR"].index(q["product_discount_type"]), key=f"epdt_{qid}")
                            epdv = st.number_input("Valor descuento productos", min_value=0.0, value=float(q["product_discount_value"]), step=1.0, disabled=epdt == "NINGUNO", key=f"epdv_{qid}")
                            einc = st.selectbox("Término", ["DAP", "DDP"], index=0 if q["incoterm"] == "DAP" else 1, key=f"einc_{qid}")
                        with ec3:
                            esdt = st.selectbox("Descuento envío", ["NINGUNO", "%", "VALOR"], index=["NINGUNO", "%", "VALOR"].index(q["shipping_discount_type"]), key=f"esdt_{qid}")
                            esdv = st.number_input("Valor descuento envío", min_value=0.0, value=float(q["shipping_discount_value"]), step=1.0, disabled=esdt == "NINGUNO", key=f"esdv_{qid}")
                        if einc == "DDP":
                            emod = st.checkbox("Modificar manualmente impuestos", value=bool(q["tax_manual"]), key=f"emod_{qid}")
                            etax = st.number_input("Impuestos", min_value=0.0, value=float(q["tax_amount"]), step=1.0, key=f"etax_{qid}") if emod else float(q["tax_per_box"] * q["boxes_count"])
                        else:
                            emod = False; etax = 0.0
                        enotes = st.text_area("Observaciones", value=q["notes"] or "", key=f"enotes_{qid}")

                        if editable and st.button("💾 Guardar cambios y recalcular", type="primary", key=f"saveq_{qid}"):
                            calc, err = q_calculate_shipping(ecountry, epweight)
                            if err:
                                st.error(err)
                            else:
                                pdisc = q_discount(eproducts, epdt, epdv)
                                sdisc = q_discount(calc["total"], esdt, esdv)
                                pnet = eproducts - pdisc; snet = calc["total"] - sdisc
                                tax = (calc["boxes"] * 35 if einc == "DDP" and not emod else etax)
                                total = pnet + snet + tax
                                valid = date.fromisoformat(q["created_at"]) + timedelta(days=15)
                                conn.execute("""UPDATE quotations SET recipient_name=?,country=?,city=?,address=?,postal_code=?,email=?,phone=?,currency=?,products_amount=?,product_discount_type=?,product_discount_value=?,product_discount_amount=?,product_net=?,product_weight=?,packaging_weight=?,billed_weight=?,boxes_count=?,shipping_base=?,shipping_discount_type=?,shipping_discount_value=?,shipping_discount_amount=?,shipping_net=?,incoterm=?,tax_amount=?,tax_per_box=?,tax_manual=?,total_amount=?,notes=?,valid_until=?,box_details=? WHERE id=?""", (
                                    ename, ecountry, ecity, eaddress, epostal, eemail, ephone, ecurrency, eproducts, epdt, epdv, pdisc, pnet, epweight, 1.0,
                                    calc["billed_weight"], calc["boxes"], calc["total"], esdt, esdv, sdisc, snet, einc, tax, 35.0, emod, total, enotes, str(valid), str(calc["details"]), qid
                                ))
                                conn.commit(); st.success("Cotización actualizada y recalculada."); st.rerun()

                        if q["status"] == "PENDIENTE":
                            a1, a2 = st.columns(2)
                            with a1:
                                if st.button("✅ Confirmar y crear pedido", key=f"confirm_{qid}"):
                                    st.session_state["prefill_order"] = {
                                        "quotation_id": q["id"], "client_id": q["client_id"], "country": q["country"], "currency": q["currency"],
                                        "products_amount": q["product_net"], "shipping_amount": q["shipping_net"], "incoterm": q["incoterm"], "tax_amount": q["tax_amount"],
                                        "tax_per_box": q["tax_per_box"], "boxes_count": q["boxes_count"], "billed_weight": q["billed_weight"], "notes": q["notes"]
                                    }
                                    st.session_state["_goto_page"] = "Nuevo pedido"; st.rerun()
                            with a2:
                                if st.button("🗑️ Descartar cotización", key=f"discard_{qid}"):
                                    conn.execute("UPDATE quotations SET status='DESCARTADA' WHERE id=?", (qid,)); conn.commit(); st.rerun()

    # ---------------- SHIPPING RATES ----------------
    with tab3:
        st.subheader("Tarifas de envío")
        st.caption("Fuente: archivo con columnas País, Kg y Tarifa. El sistema usa una tarifa por cada caja y máximo 15 kg brutos por caja (14 kg de producto + 1 kg embalaje).")
        up = st.file_uploader("Actualizar tarifas", type=["xlsx", "xls", "csv"], key="quote_rates_upload")
        if up and st.button("Actualizar tarifas", type="primary", key="update_quote_rates"):
            try:
                n = q_import_rates(up); st.success(f"{n} tarifas actualizadas."); st.rerun()
            except Exception as e:
                st.error(f"No se pudieron actualizar las tarifas: {e}")
        rates = q_load_rates()
        if rates.empty: st.info("No hay tarifas cargadas. Sube dicc_envios.xlsx.")
        else: st.dataframe(rates, use_container_width=True, hide_index=True)

# ---------------- SIDEBAR ----------------
st.sidebar.title("🌎 Raíz Latina")
st.sidebar.caption("Sistema financiero y logístico mayorista")

pages = ["Dashboard", "Nuevo pedido", "Pedidos", "Envíos pendientes", "Historial de envíos", "Clientes", "Cotizaciones", "Gastos", "Reportes", "Configuración"]
_goto = st.session_state.pop("_goto_page", None)
page = st.sidebar.radio("Módulo", pages, index=pages.index(_goto) if _goto in pages else 0, key="module_nav")

# ---------------- DASHBOARD ----------------
if page == "Dashboard":
    st.title("Dashboard")

    ref = get_reference_rates()

    # Sales include products + shipping + taxes collected from customers.
    # Taxes are treated separately because 75% is a cost and 25% remains as utility.
    total_tax_collected = conn.execute("""
        SELECT COALESCE(SUM(tax_cop),0)
        FROM orders
    """).fetchone()[0]
    total_tax_cost = total_tax_collected * 0.75

    total_sales = conn.execute("""
        SELECT COALESCE(SUM(products_sale_cop + shipping_sale_cop + COALESCE(tax_cop,0)),0)
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
        - total_tax_cost
        - total_expenses
    )
    estimated_margin = (
        estimated_result / total_sales * 100
        if total_sales > 0 else 0
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Ventas acumuladas", fmt_cop(total_sales))
    c2.metric("Costo productos", fmt_cop(total_product_cost))
    c3.metric("Costo envío", fmt_cop(total_shipping_cost))
    c4.metric("Costo impuestos", fmt_cop(total_tax_cost))
    c5.metric("Gastos registrados", fmt_cop(total_expenses))

    t1, t2 = st.columns(2)
    t1.metric("Impuestos recaudados", fmt_cop(total_tax_collected))
    t2.metric("Utilidad de impuestos (25%)", fmt_cop(total_tax_collected - total_tax_cost))

    st.divider()
    r1, r2 = st.columns(2)
    r1.metric("Resultado estimado", fmt_cop(estimated_result))
    r2.metric("Utilidad estimada", f"{estimated_margin:.2f}%")

    st.caption(
        "Resultado = ventas acumuladas − costo de productos − costo de envío − costo de impuestos − gastos registrados. "
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
    df = read_sql_query("""
        SELECT SUBSTRING(order_date FROM 1 FOR 7) AS "Mes",
               SUM(products_sale_cop + shipping_sale_cop + COALESCE(tax_cop,0)) AS "Ventas"
        FROM orders
        GROUP BY SUBSTRING(order_date FROM 1 FOR 7)
        ORDER BY "Mes"
    """, conn)
    if not df.empty:
        df["Ventas"] = df["Ventas"].round(0)
        st.bar_chart(df.set_index("Mes"))
    else:
        st.info("Todavía no hay ventas registradas.")

# ---------------- NEW ORDER ----------------
elif page == "Nuevo pedido":
    st.title("Nuevo pedido")

    prefill = st.session_state.get("prefill_order", {})
    clients = read_sql_query("SELECT id, name, country FROM clients ORDER BY name", conn)

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
            client_ids = clients["id"].tolist()
            default_client_index = client_ids.index(prefill.get("client_id")) if prefill.get("client_id") in client_ids else 0
            client_id = st.selectbox(
                "Buscar cliente", client_ids, index=default_client_index,
                format_func=lambda x: (
                    f"{clients.loc[clients.id == x, 'name'].iloc[0]} — "
                    f"{clients.loc[clients.id == x, 'country'].iloc[0] or 'Sin país'}"
                )
            )
            client_country = clients.loc[clients.id == client_id, "country"].iloc[0]
            country = st.text_input("País de destino", value=prefill.get("country", client_country or ""))

            st.subheader("Venta de productos")
            currency_options = ["EUR", "USD", "COP"]
            default_currency = prefill.get("currency", "EUR")
            currency = st.selectbox("Divisa de productos", currency_options, index=currency_options.index(default_currency) if default_currency in currency_options else 0)
            products_amount = st.number_input("Valor de productos", min_value=0.0, step=10.0, value=float(prefill.get("products_amount", 0.0)))

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
                "Valor de envío cobrado", min_value=0.0, step=5.0,
                value=float(prefill.get("shipping_amount", 0.0))
            )
            shipping_rate = {"EUR": setting("eur_cop"), "USD": setting("usd_cop"), "COP": 1}[shipping_currency]
            shipping_sale_cop = shipping_charged * shipping_rate

            st.subheader("Condiciones comerciales")
            incoterm_options = ["DAP", "DDP"]
            default_incoterm = prefill.get("incoterm", "DAP")
            incoterm = st.selectbox("Término de negociación", incoterm_options, index=incoterm_options.index(default_incoterm) if default_incoterm in incoterm_options else 0)
            tax_amount = st.number_input("Impuestos cobrados al cliente", min_value=0.0, step=1.0, value=float(prefill.get("tax_amount", 0.0)), help="Se registra por separado. No altera el margen de productos ni el costo de envío del modelo actual.")
            tax_per_box = st.number_input("Impuesto estándar por caja", min_value=0.0, step=1.0, value=float(prefill.get("tax_per_box", 35.0)))
            box_count = int(prefill.get("boxes_count", 1) or 1)
            billed_weight = float(prefill.get("billed_weight", 0) or 0)
            if box_count > 1 or billed_weight > 0:
                st.caption(f"Información interna de la cotización: {box_count} caja(s) · {billed_weight:.1f} kg facturados.")

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
            notes = st.text_area("Notas del pedido", value=prefill.get("notes", ""))

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
            tax_cop = tax_amount * {"EUR": setting("eur_cop"), "USD": setting("usd_cop"), "COP": 1}[currency]
            total_due_cop = total_sale_cop + tax_cop
            payment_cop = payment_amount * payment_rate

            if payment_amount <= 0:
                st.error("El monto recibido debe ser mayor que 0.")
                st.stop()
            if payment_type == "Pago completo" and payment_cop + 0.01 < total_due_cop:
                st.error(
                    f"Marcaste Pago completo, pero el pago registrado equivale a {fmt_cop(payment_cop)} "
                    f"y el total a pagar (incluidos impuestos) es {fmt_cop(total_due_cop)}. Si sólo pagó una parte, selecciona Abono."
                )
                st.stop()

            try:
                cur = conn.execute("""
                    INSERT INTO orders (
                        order_number, client_id, country, order_date, status,
                        products_amount_original, products_currency,
                        shipping_charged_original, shipping_currency,
                        sale_rate, products_sale_cop, shipping_sale_cop,
                        product_cost_cop, estimated_profit_cop, notes, created_at,
                        incoterm, tax_amount_original, tax_currency, tax_cop, tax_per_box, tax_manual, box_count, billed_weight, quotation_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    RETURNING id
                """, (
                    order_number.strip(), client_id, country.strip(), str(order_date),
                    "PAGO PARCIAL" if payment_type == "Abono" else "POR CONFIRMAR",
                    products_amount, currency,
                    shipping_charged, shipping_currency,
                    sale_rate, product_sale_cop, shipping_sale_cop,
                    product_cost_cop, estimated_profit, notes,
                    datetime.now().isoformat(timespec="seconds"),
                    incoterm, tax_amount, currency, tax_cop, tax_per_box, False, box_count, billed_weight, prefill.get("quotation_id")
                ))
                order_id = cur.fetchone()[0]

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
                if prefill.get("quotation_id"):
                    conn.execute("UPDATE quotations SET status='CONFIRMADA', order_id=? WHERE id=?", (order_id, int(prefill["quotation_id"])))
                    conn.commit()
                    st.session_state.pop("prefill_order", None)

                st.success(f"Pedido {order_number} creado correctamente.")
                if payment_type == "Abono":
                    saldo = max(total_due_cop - payment_cop, 0)
                    st.warning(
                        f"ABONO registrado: {fmt_cop(payment_cop)}. "
                        f"Saldo pendiente estimado: {fmt_cop(saldo)}. "
                        "El pedido aparece como PAGO PARCIAL en Envíos pendientes."
                    )
                else:
                    st.info("Estado inicial: POR CONFIRMAR → aparece automáticamente en Envíos pendientes.")
            except psycopg2.IntegrityError as e:
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

    df = read_sql_query("""
        SELECT o.id, o.order_number AS "Pedido", c.name AS "Cliente", o.country AS "País",
               o.order_date AS "Fecha",
               o.products_sale_cop AS "Valor productos",
               CASE
                   WHEN o.real_shipping_cop IS NULL THEN o.shipping_sale_cop
                   ELSE o.real_shipping_cop
               END AS "Envío COP",
               COALESCE(o.tax_cop,0) AS "Impuestos",
               (o.products_sale_cop + o.shipping_sale_cop + COALESCE(o.tax_cop,0)) AS "Venta total",
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
                        current = fetch_one("SELECT real_shipping_cop FROM orders WHERE id=?", (oid,))
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

# ---------------- ORDER DETAILS / OBSERVATIONS ----------------
    if not df.empty:
        st.divider()
        st.subheader("📝 Detalle y observaciones")
        detail_id = st.selectbox(
            "Selecciona un pedido para consultar sus notas",
            df["id"].astype(int).tolist(),
            format_func=lambda oid: f"{df.loc[df.id == oid, 'Pedido'].iloc[0]} — {df.loc[df.id == oid, 'Cliente'].iloc[0]}",
            key="order_detail_selector"
        )
        detail = fetch_one("SELECT notes, incoterm, tax_amount_original, tax_currency FROM orders WHERE id=?", (int(detail_id),))
        if detail:
            a,b,c=st.columns(3)
            a.write("**Observación del pedido**")
            a.info(detail[0] or "Sin observación")
            b.write("**Término de negociación**")
            b.info(detail[1] or "DAP")
            c.write("**Impuestos**")
            c.info(fmt_money(float(detail[2] or 0), detail[3] or "EUR"))
        payment_notes = read_sql_query("""
            SELECT payment_date AS "Fecha", amount AS "Monto", currency AS "Divisa", method AS "Método", reference AS "Referencia", notes AS "Observación"
            FROM payments WHERE order_id=? ORDER BY id DESC
        """, conn, params=(int(detail_id),))
        st.write("**Pagos y observaciones**")
        if payment_notes.empty:
            st.caption("Este pedido todavía no tiene pagos registrados.")
        else:
            st.dataframe(payment_notes, use_container_width=True, hide_index=True)

# ---------------- SHIPPING ----------------
elif page == "Envíos pendientes":
    st.title("📦 Envíos pendientes")
    st.caption(
        "Aquí aparecen pedidos que todavía no están ENVIADOS. "
        "Los pedidos con PAGO PARCIAL se muestran completos en rojo para identificarlos rápidamente."
    )

    df = read_sql_query("""
        SELECT o.id, o.order_number AS "Pedido", c.name AS "Cliente", o.country AS "País",
               o.order_date AS "Fecha", o.status AS "Estado"
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
                oid = int(row["id"])
                old_status = row["Estado"]
                if new_status == "ENVIADO":
                    conn.execute("""
                        INSERT INTO shipment_history(order_id, previous_status, shipped_at)
                        VALUES (?,?,?)
                    """, (oid, old_status, datetime.now().isoformat(timespec="seconds")))
                conn.execute("UPDATE orders SET status=? WHERE id=?", (new_status, oid))
                conn.commit()
                st.rerun()

# ---------------- SHIPMENT HISTORY ----------------
elif page == "Historial de envíos":
    st.title("🚚 Historial de envíos")
    st.caption("Aquí quedan registrados los pedidos que fueron marcados como ENVIADO. Si generas una devolución, el pedido vuelve automáticamente a Envíos pendientes y el historial conserva el registro de la devolución.")

    history_df = read_sql_query("""
        SELECT sh.id, o.order_number AS "Pedido", c.name AS "Cliente",
               o.country AS "País", o.order_date AS "Fecha pedido",
               sh.shipped_at AS "Fecha envío",
               CASE WHEN sh.returned_at IS NULL THEN 'ENVIADO' ELSE 'DEVUELTO' END AS "Estado envío",
               sh.returned_at AS "Fecha devolución",
               sh.return_notes AS "Notas devolución"
        FROM shipment_history sh
        JOIN orders o ON o.id=sh.order_id
        JOIN clients c ON c.id=o.client_id
        ORDER BY sh.shipped_at DESC, sh.id DESC
    """, conn)

    if history_df.empty:
        st.info("Todavía no hay envíos registrados en el historial.")
    else:
        export_df = history_df.drop(columns=["id"]).copy()
        st.download_button(
            "⬇️ Exportar historial a Excel",
            excel_bytes(export_df),
            "historial_envios_raiz_latina.xlsx",
            type="primary"
        )
        st.dataframe(export_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Gestionar devoluciones")
        active = history_df[history_df["Estado envío"] == "ENVIADO"]
        if active.empty:
            st.info("No hay envíos actualmente ENVIADOS para devolver.")
        else:
            for _, row in active.iterrows():
                with st.container(border=True):
                    c1,c2,c3,c4 = st.columns([1.5,2.5,1.5,2])
                    c1.markdown(f"**{row['Pedido']}**")
                    c2.write(str(row['Cliente']))
                    c3.write(str(row['País']))
                    c4.write(f"Enviado: {row['Fecha envío']}")
                    return_note = st.text_input(
                        "Motivo / nota de devolución (opcional)",
                        key=f"return_note_{int(row['id'])}"
                    )
                    if st.button("↩️ Generar devolución", key=f"return_{int(row['id'])}"):
                        shipment = conn.execute(
                            "SELECT order_id, previous_status FROM shipment_history WHERE id=? AND returned_at IS NULL",
                            (int(row["id"]),)
                        ).fetchone()
                        if shipment:
                            order_id, previous_status = shipment
                            restore_status = previous_status if previous_status in [
                                "PAGO PARCIAL", "POR CONFIRMAR", "MERCANCÍA SOLICITADA", "LISTO PARA ENVÍO"
                            ] else "LISTO PARA ENVÍO"
                            conn.execute(
                                "UPDATE orders SET status=? WHERE id=?",
                                (restore_status, int(order_id))
                            )
                            conn.execute(
                                "UPDATE shipment_history SET returned_at=?, return_notes=? WHERE id=? AND returned_at IS NULL",
                                (datetime.now().isoformat(timespec="seconds"), return_note.strip(), int(row["id"]))
                            )
                            conn.commit()
                            st.success(f"{row['Pedido']} volvió a Envíos pendientes con estado: {restore_status}.")
                            st.rerun()
                        else:
                            st.warning("Este envío ya fue devuelto o no está disponible para devolución.")

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

        clients_df = read_sql_query("""
            SELECT id AS "ID", name AS "Cliente", country AS "País", city AS "Ciudad",
                   address AS "Dirección", postal_code AS "Código postal",
                   email AS "Email", phone AS "Teléfono"
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

                client = fetch_one("""
                    SELECT id,name,country,city,address,postal_code,email,phone,notes,created_at
                    FROM clients WHERE id=?
                """, (int(selected_id),))

                if client:
                    (
                        cid, cname, ccountry, ccity, caddress, cpostal,
                        cemail, cphone, cnotes, ccreated
                    ) = client

                    stats = fetch_one("""
                        SELECT COUNT(*),
                               COALESCE(SUM(products_sale_cop + shipping_sale_cop),0)
                        FROM orders WHERE client_id=?
                    """, (cid,))

                    m1, m2 = st.columns(2)
                    m1.metric("Pedidos realizados", int(stats[0] or 0))
                    m2.metric("Total comprado", fmt_cop(float(stats[1] or 0)))

                    st.markdown("#### Historial de pedidos")
                    history = read_sql_query("""
                        SELECT order_number AS "Pedido", order_date AS "Fecha",
                               country AS "País",
                               (products_sale_cop + shipping_sale_cop + COALESCE(tax_cop,0)) AS "Total COP",
                               status AS "Estado"
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

elif page == "Cotizaciones":
    render_quotations()

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

    df = read_sql_query("""
        SELECT expense_date AS "Fecha", category AS "Categoría", description AS "Descripción",
               amount AS "Valor", currency AS "Divisa", rate AS "Tasa",
               cop_amount AS "COP", method AS "Método", supplier AS "Proveedor"
        FROM expenses ORDER BY expense_date DESC, id DESC
    """, conn)
    st.subheader("Gastos registrados")
    st.dataframe(df, use_container_width=True, hide_index=True)

# ---------------- REPORTS ----------------
elif page == "Reportes":
    st.title("📊 Reportes")

    start = st.date_input("Desde", value=date(date.today().year, 1, 1))
    end = st.date_input("Hasta", value=date.today())

    sales = read_sql_query("""
        SELECT o.order_number AS "Pedido", c.name AS "Cliente", o.country AS "País",
               o.order_date AS "Fecha", o.status AS "Estado",
               o.products_amount_original AS "Productos_Original",
               o.products_currency AS "Divisa_Productos",
               o.products_sale_cop AS "Productos_COP",
               o.shipping_charged_original AS "Envío_Cobrado_Original",
               o.shipping_currency AS "Divisa_Envío",
               o.shipping_sale_cop AS "Envío_Cobrado_COP",
               o.product_cost_cop AS "Costo_Productos_COP",
               o.real_shipping_original AS "Envío_Real_Original",
               o.real_shipping_currency AS "Divisa_Envío_Real",
               o.real_shipping_cop AS "Envío_Real_COP",
               COALESCE(o.tax_cop,0) AS "Impuestos_COP",
               (COALESCE(o.tax_cop,0) * 0.75) AS "Costo_Impuestos_COP",
               (COALESCE(o.tax_cop,0) * 0.25) AS "Utilidad_Impuestos_COP",
               (o.products_sale_cop + o.shipping_sale_cop + COALESCE(o.tax_cop,0)) AS "Venta_Total_COP",
               (o.products_sale_cop - o.product_cost_cop) AS "Utilidad_Productos_COP",
               CASE
                   WHEN o.real_shipping_cop IS NULL THEN NULL
                   ELSE (o.products_sale_cop - o.product_cost_cop
                         + o.shipping_sale_cop - o.real_shipping_cop
                         + COALESCE(o.tax_cop,0) * 0.25)
               END AS "Utilidad_Real_Pedido_COP"
        FROM orders o JOIN clients c ON c.id=o.client_id
        WHERE CAST(o.order_date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
        ORDER BY o.order_date
    """, conn, params=(str(start), str(end)))

    expenses = read_sql_query("""
        SELECT expense_date AS "Fecha", category AS "Categoría", description AS "Descripción",
               amount AS "Valor", currency AS "Divisa", rate AS "Tasa", cop_amount AS "COP",
               method AS "Método", supplier AS "Proveedor"
        FROM expenses
        WHERE CAST(expense_date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
        ORDER BY expense_date
    """, conn, params=(str(start), str(end)))

    payments = read_sql_query("""
        SELECT p.payment_date AS "Fecha", o.order_number AS "Pedido", c.name AS "Cliente",
               p.amount AS "Monto", p.currency AS "Divisa", p.rate AS "Tasa",
               p.cop_amount AS "COP", p.method AS "Método", p.reference AS "Referencia"
        FROM payments p
        JOIN orders o ON o.id=p.order_id
        JOIN clients c ON c.id=o.client_id
        WHERE CAST(p.payment_date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
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
    tax_collected = sales["Impuestos_COP"].sum() if not sales.empty else 0
    tax_cost = sales["Costo_Impuestos_COP"].sum() if not sales.empty else 0
    shipping_real = sales["Envío_Real_COP"].dropna().sum() if not sales.empty else 0
    pending_shipping = int(sales["Envío_Real_COP"].isna().sum()) if not sales.empty else 0
    shipping_charged = sales["Envío_Cobrado_COP"].sum() if not sales.empty else 0
    exp_total = expenses["COP"].sum() if not expenses.empty else 0

    # Estimated result: use the amount charged for shipping as provisional shipping cost.
    estimated_result = sales_total - product_cost - shipping_charged - tax_cost - exp_total

    # Available result: actual shipping where entered, charged shipping where still pending.
    if not sales.empty:
        available_shipping_cost = (
            sales["Envío_Real_COP"].fillna(sales["Envío_Cobrado_COP"]).sum()
        )
    else:
        available_shipping_cost = 0
    available_result = sales_total - product_cost - available_shipping_cost - tax_cost - exp_total

    r1,r2,r3,r4,r5 = st.columns(5)
    r1.metric("Ventas", fmt_cop(sales_total))
    r2.metric("Costo productos", fmt_cop(product_cost))
    r3.metric("Costo impuestos", fmt_cop(tax_cost))
    r4.metric("Gastos", fmt_cop(exp_total))
    r5.metric("Resultado estimado", fmt_cop(estimated_result))
    st.caption(f"Impuestos recaudados en el período: {fmt_cop(tax_collected)} · costo de impuestos (75%): {fmt_cop(tax_cost)} · utilidad de impuestos (25%): {fmt_cop(tax_collected-tax_cost)}")
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
    st.caption("La base de datos se guarda de forma permanente en PostgreSQL (Neon). Los datos no dependen de la carpeta de la aplicación.")
    if st.button("Cerrar conexión y recargar"):
        conn.close()
        st.rerun()
