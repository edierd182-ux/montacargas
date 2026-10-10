import sqlite3
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = "fleet_full.db"

INTERVAL_HOURS = 250
WARN_HOURS = 50
URGENT_HOURS = 25

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA foreign_keys = ON")
    
    c.executescript("""
    CREATE TABLE IF NOT EXISTS plants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        location TEXT DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','tech')),
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_plants (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
        PRIMARY KEY(user_id, plant_id)
    );
    CREATE TABLE IF NOT EXISTS equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        make_model TEXT DEFAULT '',
        serial_number TEXT DEFAULT '',
        fuel_type TEXT DEFAULT '',
        reference TEXT DEFAULT '',
        engine TEXT DEFAULT '',
        plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
        active INTEGER NOT NULL DEFAULT 1,
        notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
        hourmeter REAL NOT NULL CHECK(hourmeter >= 0),
        technician_id INTEGER NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        note TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS maintenance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
        service_hourmeter REAL NOT NULL CHECK(service_hourmeter >= 0),
        service_date TEXT NOT NULL,
        service_type TEXT NOT NULL DEFAULT 'Preventivo 250 h',
        performed_by INTEGER REFERENCES users(id),
        note TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS breakdowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'FALLANDO',
        reported_at TEXT NOT NULL,
        resolved_at TEXT,
        reported_by INTEGER REFERENCES users(id),
        note TEXT DEFAULT ''
    );
    """)

    # Crear admin por defecto si no existe
    c.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
    if not c.fetchone():
        hashed = generate_password_hash("Admin12345678")
        c.execute(
            "INSERT INTO users(username, display_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
            ("ADMIN001", "Administrador", hashed, "admin", utc_now())
        )
    conn.commit()
    conn.close()

init_db()

st.set_page_config(page_title="Control de Flota y Mantenimiento", page_icon="🚜", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .stAlert { border-radius: 8px; }
    </style>
""", unsafe_allow_html=True)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def equipment_status(db, item):
    reading = db.execute(
        "SELECT r.*, u.display_name AS technician_name FROM readings r "
        "JOIN users u ON u.id=r.technician_id WHERE equipment_id=? ORDER BY r.id DESC LIMIT 1",
        (item["id"],)
    ).fetchone()
    
    maintenance = db.execute(
        "SELECT m.*, u.display_name AS technician_name FROM maintenance m "
        "LEFT JOIN users u ON u.id=m.performed_by WHERE equipment_id=? "
        "ORDER BY service_hourmeter DESC, id DESC LIMIT 1",
        (item["id"],)
    ).fetchone()
    
    current = reading["hourmeter"] if reading else None
    service_hm = maintenance["service_hourmeter"] if maintenance else None
    next_due = service_hm + INTERVAL_HOURS if service_hm is not None else None
    remaining = next_due - current if next_due is not None and current is not None else None
    
    if current is None or service_hm is None:
        status = "FALTA DATO"
    elif remaining <= 0:
        status = "VENCIDO"
    elif remaining <= URGENT_HOURS:
        status = "URGENTE"
    elif remaining <= WARN_HOURS:
        status = "PROGRAMAR"
    else:
        status = "AL DÍA"
        
    active_breakdown = db.execute("SELECT * FROM breakdowns WHERE equipment_id=? AND resolved_at IS NULL", (item["id"],)).fetchone()
    if active_breakdown:
        status = "EN FALLA"
        
    return {
        "item": item, "reading": reading, "maintenance": maintenance,
        "current": current, "service_hm": service_hm, "next_due": next_due,
        "remaining": remaining, "status": status, "active_breakdown": active_breakdown
    }

if not st.session_state.logged_in:
    st.title("🚜 Control de Flota y Mantenimiento")
    st.info("Credenciales por defecto: **ADMIN001** | Contraseña: **Admin12345678**")
    
    with st.form("login_form"):
        username = st.text_input("Usuario").strip().upper()
        password = st.text_input("Contraseña", type="password")
        submit = st.form_submit_button("Ingresar al Sistema")
        
        if submit:
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
            db.close()
            
            if user and check_password_hash(user["password_hash"], password):
                st.session_state.logged_in = True
                st.session_state.user = dict(user)
                st.success("¡Bienvenido!")
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos, o cuenta inactiva.")
else:
    user = st.session_state.user
    st.sidebar.title(f"👤 {user['display_name']}")
    st.sidebar.markdown(f"Rol: **{user['role'].upper()}**")
    
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.logged_in = False
        st.session_state.user = None
        st.rerun()

    if user["role"] == "admin":
        menu_options = ["Dashboard de Flota", "Panel de Administración"]
    else:
        menu_options = ["Dashboard de Flota", "Registrar Lecturas (Horómetros)", "Registrar Mantenimiento", "Estado y Fallas de Equipos"]
        
    choice = st.sidebar.selectbox("Menú Principal", menu_options)
    
    db = get_db()
    
    def allowed_plant_ids():
        if user["role"] == "admin":
            rows = db.execute("SELECT id FROM plants WHERE active=1").fetchall()
            return [r["id"] for r in rows]
        rows = db.execute("SELECT plant_id FROM user_plants WHERE user_id=?", (user["id"],)).fetchall()
        return [r["plant_id"] for r in rows]

    if choice == "Dashboard de Flota":
        st.header("📋 Dashboard General de Flota")
        p_ids = allowed_plant_ids()
        if p_ids:
            marks = ",".join("?" for _ in p_ids)
            rows = db.execute(
                f"SELECT e.*, p.name AS plant_name FROM equipment e JOIN plants p ON p.id=e.plant_id "
                f"WHERE e.active=1 AND e.plant_id IN ({marks}) ORDER BY p.name, e.code", p_ids
            ).fetchall()
        else:
            rows = []
            
        if not rows:
            st.warning("No hay equipos asignados o disponibles para tus proyectos actuales.")
        else:
            statuses = [equipment_status(db, row) for row in rows]
            
            counts = {key: sum(1 for x in statuses if x["status"] == key)
                      for key in ["AL DÍA", "PROGRAMAR", "URGENTE", "VENCIDO", "EN FALLA", "FALTA DATO"]}
            
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            col1.metric("🟢 Al Día", counts["AL DÍA"])
            col2.metric("🟡 Programar", counts["PROGRAMAR"])
            col3.metric("🟠 Urgente", counts["URGENTE"])
            col4.metric("🔴 Vencido", counts["VENCIDO"])
            col5.metric("⚠️ En Falla", counts["EN FALLA"])
            col6.metric("⚪ Falta Dato", counts["FALTA DATO"])
            
            st.markdown("---")
            
            st.subheader("📊 Gráfico de Distribución de Estados")
            chart_data = pd.DataFrame(list(counts.items()), columns=["Estado", "Cantidad"]).set_index("Estado")
            st.bar_chart(chart_data)
            
            st.markdown("---")
            
            st.subheader("🚨 Equipos Fuera de Servicio / Sin Operar")
            down_equipments = [s for s in statuses if s["status"] == "EN FALLA"]
            
            if down_equipments:
                for de in down_equipments:
                    item = de["item"]
                    ab = de["active_breakdown"]
                    st.error(f"⚠️ **[{item['plant_name']}] Equipo {item['code']} - {item['make_model']}**\n- Reportado el: `{ab['reported_at']}`\n- Motivo/Nota: {ab['note'] or 'Sin notas'}")
            else:
                st.success("🟢 Todos los equipos en tu alcance se encuentran operativos.")
            
            st.markdown("---")
            st.subheader("🔍 Listado Detallado de Equipos")
            for st_item in statuses:
                item = st_item["item"]
                status = st_item["status"]
                color_map = {"AL DÍA": "🟢", "PROGRAMAR": "🟡", "URGENTE": "🟠", "VENCIDO": "🔴", "EN FALLA": "⚠️", "FALTA DATO": "⚪"}
                
                with st.expander(f"{color_map.get(status, '⚪')} [{status}] Equipo: {item['code']} - {item['make_model']} (Proyecto: {item['plant_name']})"):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.write(f"**Referencia:** {item['reference'] or 'N/A'}")
                        st.write(f"**Combustible:** {item['fuel_type'] or 'N/A'}")
                        st.write(f"**Motor:** {item['engine'] or 'N/A'}")
                        st.write(f"**Nº Serie:** {item['serial_number'] or 'N/A'}")
                    with col_b:
                        st.write(f"**Horómetro Actual:** {st_item['current'] if st_item['current'] is not None else 'Sin lecturas'} hrs")
                        st.write(f"**Último Servicio:** {st_item['service_hm'] if st_item['service_hm'] is not None else 'Sin mantenimiento'} hrs")
                        st.write(f"**Próximo Mantenimiento:** {st_item['next_due'] if st_item['next_due'] is not None else 'N/D'} hrs")
                        st.write(f"**Horas Restantes:** {st_item['remaining'] if st_item['remaining'] is not None else 'N/D'} hrs")
                    
                    if st_item["active_breakdown"]:
                        ab = st_item["active_breakdown"]
                        st.error(f"⚠️ **Equipo reportado en falla** desde: {ab['reported_at']} | Motivo: {ab['note'] or 'Sin notas'}")

                    st.markdown("---")
                    st.markdown("##### Historial de Mantenimientos")
                    history = db.execute("SELECT m.*, u.display_name AS technician_name FROM maintenance m LEFT JOIN users u ON u.id=m.performed_by WHERE equipment_id=? ORDER BY service_hourmeter DESC", (item["id"],)).fetchall()
                    if history:
                        for h in history:
                            st.write(f"- Fecha: **{h['service_date']}** | Horómetro de Servicio: **{h['service_hourmeter']:g} hrs** | Tipo: {h['service_type']} | Realizado por: {h['technician_name'] or 'N/A'} | Nota: {h['note'] or 'Ninguna'}")
                    else:
                        st.info("No hay registros de mantenimiento previos para este equipo.")

    elif choice == "Registrar Lecturas (Horómetros)" and user["role"] == "tech":
        st.header("⏱️ Registro Masivo de Horómetros")
        st.markdown("Ingresa la lectura actual para cada equipo de tu proyecto y haz clic en enviar al final.")
        
        p_ids = allowed_plant_ids()
        if p_ids:
            marks = ",".join("?" for _ in p_ids)
            equipment_list = db.execute(
                f"SELECT e.*, p.name AS plant_name FROM equipment e JOIN plants p ON p.id=e.plant_id "
                f"WHERE e.active=1 AND e.plant_id IN ({marks}) ORDER BY p.name, e.code", p_ids
            ).fetchall()
        else:
            equipment_list = []
            
        if not equipment_list:
            st.warning("No tienes equipos disponibles para registrar lecturas.")
        else:
            with st.form("bulk_readings_form"):
                inputs = {}
                for eq in equipment_list:
                    prev = db.execute("SELECT hourmeter FROM readings WHERE equipment_id=? ORDER BY id DESC LIMIT 1", (eq["id"],)).fetchone()
                    prev_val = prev["hourmeter"] if prev else 0.0
                    
                    st.markdown(f"**🚜 {eq['code']}** — *{eq['make_model']}* (Proyecto: **{eq['plant_name']}**) | Último registrado: `{prev_val:g} hrs`")
                    inputs[eq["id"]] = st.number_input(f"Nuevo Horómetro para {eq['code']}", min_value=0.0, value=prev_val, step=0.1, key=f"eq_in_{eq['id']}")
                    st.markdown("---")
                
                submitted = st.form_submit_button("Enviar Todas las Lecturas")
                
                if submitted:
                    error_found = False
                    for eq_id, new_val in inputs.items():
                        prev = db.execute("SELECT hourmeter FROM readings WHERE equipment_id=? ORDER BY id DESC LIMIT 1", (eq_id,)).fetchone()
                        if prev and new_val < prev["hourmeter"]:
                            st.error(f"El equipo ID {eq_id} tiene una lectura menor al último registro ({new_val:g} < {prev['hourmeter']:g}). No se guardó ningún cambio.")
                            error_found = True
                            break
                    
                    if not error_found:
                        for eq_id, new_val in inputs.items():
                            db.execute(
                                "INSERT INTO readings(equipment_id, hourmeter, technician_id, created_at, note) VALUES(?,?,?,?,?)",
                                (eq_id, new_val, user["id"], utc_now(), "Registro masivo técnico")
                            )
                        db.commit()
                        st.success("¡Todas las lecturas de horómetros fueron guardadas exitosamente!")
                        st.rerun()

    elif choice == "Registrar Mantenimiento" and user["role"] == "tech":
        st.header("🛠️ Registrar Mantenimiento Realizado")
        st.markdown("Registra el servicio técnico realizado a los equipos de tus proyectos para actualizar el ciclo de las 250 horas.")
        
        p_ids = allowed_plant_ids()
        if p_ids:
            marks = ",".join("?" for _ in p_ids)
            eqs = db.execute(
                f"SELECT e.*, p.name AS plant_name FROM equipment e JOIN plants p ON p.id=e.plant_id "
                f"WHERE e.active=1 AND e.plant_id IN ({marks}) ORDER BY p.name, e.code", p_ids
            ).fetchall()
        else:
            eqs = []

        if not eqs:
            st.warning("No hay equipos activos disponibles en tus proyectos.")
        else:
            eq_dict = {f"{e['code']} ({e['make_model']} - {e['plant_name']})": e["id"] for e in eqs}
            with st.form("maint_form"):
                m_eq_label = st.selectbox("Seleccionar Equipo", list(eq_dict.keys()))
                m_hm = st.number_input("Horómetro exacto al realizar el mantenimiento", min_value=0.0, step=0.1)
                m_date = st.date_input("Fecha del Servicio", value=datetime.now().date())
                m_type = st.text_input("Tipo de Servicio", value="Preventivo 250 h")
                m_note = st.text_area("Notas / Observaciones del Mantenimiento").strip()
                m_submit = st.form_submit_button("Guardar Mantenimiento")
                
                if m_submit:
                    db.execute(
                        "INSERT INTO maintenance(equipment_id, service_hourmeter, service_date, service_type, performed_by, note, created_at) VALUES(?,?,?,?,?,?,?)",
                        (eq_dict[m_eq_label], m_hm, str(m_date), m_type, user["id"], m_note, utc_now())
                    )
                    db.commit()
                    st.success("¡Mantenimiento registrado correctamente!")
                    st.rerun()

    elif choice == "Estado y Fallas de Equipos" and user["role"] == "tech":
        st.header("⚠️ Control de Estado y Reporte de Fallas")
        st.markdown("Selecciona un equipo de la lista desplegable para verificar su estado, reportar una falla o marcarlo como operativo.")
        
        p_ids = allowed_plant_ids()
        if p_ids:
            marks = ",".join("?" for _ in p_ids)
            eqs = db.execute(
                f"SELECT e.*, p.name AS plant_name FROM equipment e JOIN plants p ON p.id=e.plant_id "
                f"WHERE e.active=1 AND e.plant_id IN ({marks}) ORDER BY p.name, e.code", p_ids
            ).fetchall()
        else:
            eqs = []

        if not eqs:
            st.warning("No tienes equipos asignados.")
        else:
            eq_dict = {f"{e['code']} ({e['make_model']} - Proyecto: {e['plant_name']})": e for e in eqs}
            selected_eq_label = st.selectbox("Seleccionar Equipo", list(eq_dict.keys()))
            eq = eq_dict[selected_eq_label]
            
            st.markdown("---")
            st.subheader(f"🚜 Equipo seleccionado: {eq['code']} - {eq['make_model']}")
            active_b = db.execute("SELECT * FROM breakdowns WHERE equipment_id=? AND resolved_at IS NULL", (eq["id"],)).fetchone()
            
            if active_b:
                st.error(f"⚠️ **Estado Actual: EN FALLA**\n- Reportado el: `{active_b['reported_at']}`\n- Motivo: {active_b['note'] or 'Sin descripción'}")
                if st.button(f"Marcar como Operativo (Reparado) - {eq['code']}"):
                    now_str = utc_now()
                    db.execute("UPDATE breakdowns SET resolved_at=? WHERE id=?", (now_str, active_b["id"]))
                    db.commit()
                    st.success(f"¡El equipo {eq['code']} ha sido puesto operativo nuevamente!")
                    st.rerun()
            else:
                st.success("🟢 **Estado Actual: Operativo / Buen Estado**")
                with st.form(f"report_falla_{eq['id']}"):
                    falla_note = st.text_input("Descripción de la falla (opcional)")
                    report_submit = st.form_submit_button("Reportar Falla")
                    if report_submit:
                        db.execute(
                            "INSERT INTO breakdowns(equipment_id, status, reported_at, reported_by, note) VALUES(?,?,?,?,?)",
                            (eq["id"], "FALLANDO", utc_now(), user["id"], falla_note)
                        )
                        db.commit()
                        st.error(f"¡Falla reportada para el equipo {eq['code']}!")
                        st.rerun()
            
            st.markdown("---")
            with st.expander(f"Ver historial de tiempos y fallas de {eq['code']}"):
                b_history = db.execute("SELECT * FROM breakdowns WHERE equipment_id=? ORDER BY id DESC", (eq["id"],)).fetchall()
                if b_history:
                    for bh in b_history:
                        resolved = bh["resolved_at"] if bh["resolved_at"] else "Aún en falla / No resuelto"
                        duracion_txt = "En curso"
                        if bh["resolved_at"]:
                            try:
                                t1 = datetime.fromisoformat(bh["reported_at"])
                                t2 = datetime.fromisoformat(bh["resolved_at"])
                                diff = t2 - t1
                                horas_inactivo = diff.total_seconds() / 3600
                                duracion_txt = f"{horas_inactivo:.2f} horas ({diff})"
                            except Exception:
                                pass
                        st.write(f"- 🔴 **Reporte:** {bh['reported_at']} | 🟢 **Solución:** {resolved} | ⏱️ **Tiempo inactivo:** **{duracion_txt}** | Nota: {bh['note'] or 'N/A'}")
                else:
                    st.info("Sin registros históricos de fallas.")

    elif choice == "Panel de Administración" and user["role"] == "admin":
        st.header("⚙️ Panel de Administración")
        
        tab1, tab2, tab3 = st.tabs(["Proyectos / Plantas", "Equipos (Montacargas)", "Usuarios y Técnicos"])
        
        with tab1:
            st.subheader("Gestión de Proyectos / Plantas")
            with st.form("plant_form"):
                p_name = st.text_input("Nombre del Proyecto / Planta").strip()
                p_loc = st.text_input("Ubicación / Dirección").strip()
                p_submit = st.form_submit_button("Crear Proyecto")
                if p_submit:
                    if p_name:
                        try:
                            db.execute("INSERT INTO plants(name, location) VALUES(?,?)", (p_name, p_loc))
                            db.commit()
                            st.success("¡Proyecto creado con éxito!")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Ya existe un proyecto con ese nombre.")
                    else:
                        st.warning("El nombre del proyecto es obligatorio.")
            
            st.markdown("### Proyectos Existentes y Edición")
            plants = db.execute("SELECT * FROM plants ORDER BY name").fetchall()
            for p in plants:
                with st.expander(f"🏢 {p['name']} (Ubicación: {p['location'] or 'N/A'})"):
                    with st.form(f"edit_plant_{p['id']}"):
                        ed_name = st.text_input("Nombre", value=p["name"], key=f"en_{p['id']}")
                        ed_loc = st.text_input("Ubicación", value=p["location"], key=f"el_{p['id']}")
                        up_p = st.form_submit_button("Guardar Cambios")
                        if up_p:
                            db.execute("UPDATE plants SET name=?, location=? WHERE id=?", (ed_name, ed_loc, p["id"]))
                            db.commit()
                            st.success("¡Proyecto actualizado!")
                            st.rerun()
                    if st.button("Eliminar Proyecto", key=f"del_plant_{p['id']}"):
                        db.execute("DELETE FROM plants WHERE id=?", (p["id"],))
                        db.commit()
                        st.success(f"Proyecto {p['name']} eliminado.")
                        st.rerun()

        with tab2:
            st.subheader("Registrar Nuevo Montacargas / Equipo")
            plants = db.execute("SELECT id, name FROM plants WHERE active=1 ORDER BY name").fetchall()
            if not plants:
                st.warning("Primero debes crear al menos un Proyecto / Planta.")
            else:
                plant_dict = {p["name"]: p["id"] for p in plants}
                with st.form("eq_form"):
                    eq_code = st.text_input("Código del Equipo (Ej. MNT-001)").strip().upper()
                    eq_model = st.text_input("Marca / Modelo (Ej. Toyota 3 Ton)").strip()
                    eq_ref = st.text_input("Referencia").strip()
                    eq_fuel = st.selectbox("Tipo de Combustible", ["Gasolina / Gas", "Diesel", "Eléctrico"])
                    eq_engine = st.text_input("Motor").strip()
                    eq_serial = st.text_input("Número de Serie").strip()
                    initial_hm = st.number_input("Horómetro Inicial / Actual", min_value=0.0, step=0.1, value=0.0)
                    initial_maint_hm = st.number_input("Horómetro del Último Mantenimiento (Base para los cálculos)", min_value=0.0, step=0.1, value=0.0)
                    eq_plant_label = st.selectbox("Asignar a Proyecto / Planta", list(plant_dict.keys()))
                    eq_notes = st.text_area("Notas adicionales").strip()
                    eq_submit = st.form_submit_button("Guardar Montacargas")
                    
                    if eq_submit:
                        if eq_code:
                            try:
                                cur = db.execute(
                                    "INSERT INTO equipment(code, make_model, serial_number, fuel_type, reference, engine, plant_id, notes) VALUES(?,?,?,?,?,?,?,?)",
                                    (eq_code, eq_model, eq_serial, eq_fuel, eq_ref, eq_engine, plant_dict[eq_plant_label], eq_notes)
                                )
                                new_eq_id = cur.lastrowid
                                # Registrar horómetro actual/inicial
                                db.execute(
                                    "INSERT INTO readings(equipment_id, hourmeter, technician_id, created_at, note) VALUES(?,?,?,?,?)",
                                    (new_eq_id, initial_hm, user["id"], utc_now(), "Horómetro inicial de creación")
                                )
                                # Registrar mantenimiento base inicial
                                db.execute(
                                    "INSERT INTO maintenance(equipment_id, service_hourmeter, service_date, service_type, performed_by, note, created_at) VALUES(?,?,?,?,?,?,?)",
                                    (new_eq_id, initial_maint_hm, str(datetime.now().date()), "Preventivo Base", user["id"], "Mantenimiento base inicial", utc_now())
                                )
                                db.commit()
                                st.success("¡Montacargas registrado con éxito y bases de horómetros configuradas!")
                                st.rerun()
                            except sqlite3.IntegrityError:
                                st.error("El código del equipo ya existe.")
                        else:
                            st.warning("El código del equipo es obligatorio.")
                            
            st.markdown("---")
            st.subheader("Equipos Registrados (Filtro por Proyecto y Edición)")
            
            all_plants_filter = db.execute("SELECT id, name FROM plants ORDER BY name").fetchall()
            filter_options = ["Todos los proyectos"] + [p["name"] for p in all_plants_filter]
            selected_proj_filter = st.selectbox("Filtrar vista de equipos por proyecto", filter_options)
            
            if selected_proj_filter == "Todos los proyectos":
                eqs = db.execute("SELECT e.*, p.name AS plant_name FROM equipment e JOIN plants p ON p.id=e.plant_id ORDER BY p.name, e.code").fetchall()
            else:
                eqs = db.execute("SELECT e.*, p.name AS plant_name FROM equipment e JOIN plants p ON p.id=e.plant_id WHERE p.name=? ORDER BY e.code", (selected_proj_filter,)).fetchall()
            
            if not eqs:
                st.info("No hay equipos registrados bajo este criterio.")
            else:
                for eq in eqs:
                    st_info = equipment_status(db, eq)
                    with st.expander(f"🚜 {eq['code']} | {eq['make_model']} (Proyecto: {eq['plant_name']}) - Estado: {st_info['status']}"):
                        with st.form(f"edit_eq_{eq['id']}"):
                            ed_code = st.text_input("Código", value=eq["code"], key=f"eco_{eq['id']}")
                            ed_model = st.text_input("Marca / Modelo", value=eq["make_model"], key=f"emm_{eq['id']}")
                            ed_ref = st.text_input("Referencia", value=eq["reference"], key=f"eref_{eq['id']}")
                            fuel_list = ["Gasolina / Gas", "Diesel", "Eléctrico"]
                            f_idx = fuel_list.index(eq["fuel_type"]) if eq["fuel_type"] in fuel_list else 0
                            ed_fuel = st.selectbox("Combustible", fuel_list, index=f_idx, key=f"efuel_{eq['id']}")
                            ed_engine = st.text_input("Motor", value=eq["engine"], key=f"eeng_{eq['id']}")
                            ed_serial = st.text_input("Nº Serie", value=eq["serial_number"], key=f"eserial_{eq['id']}")
                            ed_notes = st.text_area("Notas", value=eq["notes"], key=f"enotes_{eq['id']}")
                            
                            cur_val = st_info["current"] if st_info["current"] is not None else 0.0
                            maint_val = st_info["service_hm"] if st_info["service_hm"] is not None else 0.0
                            
                            ed_current_hm = st.number_input("Horómetro Actual", min_value=0.0, value=float(cur_val), step=0.1, key=f"ec_hm_{eq['id']}")
                            ed_maint_hm = st.number_input("Horómetro del Último Mantenimiento (Base)", min_value=0.0, value=float(maint_val), step=0.1, key=f"em_hm_{eq['id']}")
                            
                            up_eq = st.form_submit_button("Actualizar Datos del Equipo")
                            if up_eq:
                                db.execute("UPDATE equipment SET code=?, make_model=?, reference=?, fuel_type=?, engine=?, serial_number=?, notes=? WHERE id=?", 
                                           (ed_code, ed_model, ed_ref, ed_fuel, ed_engine, ed_serial, ed_notes, eq["id"]))
                                
                                # Actualizar o insertar lectura actual
                                latest_r = db.execute("SELECT id FROM readings WHERE equipment_id=? ORDER BY id DESC LIMIT 1", (eq["id"],)).fetchone()
                                if latest_r:
                                    db.execute("UPDATE readings SET hourmeter=? WHERE id=?", (ed_current_hm, latest_r["id"]))
                                else:
                                    db.execute("INSERT INTO readings(equipment_id, hourmeter, technician_id, created_at, note) VALUES(?,?,?,?,?)",
                                               (eq["id"], ed_current_hm, user["id"], utc_now(), "Actualización administrador"))
                                
                                # Actualizar o insertar mantenimiento base
                                latest_m = db.execute("SELECT id FROM maintenance WHERE equipment_id=? ORDER BY service_hourmeter DESC, id DESC LIMIT 1", (eq["id"],)).fetchone()
                                if latest_m:
                                    db.execute("UPDATE maintenance SET service_hourmeter=? WHERE id=?", (ed_maint_hm, latest_m["id"]))
                                else:
                                    db.execute("INSERT INTO maintenance(equipment_id, service_hourmeter, service_date, service_type, performed_by, note, created_at) VALUES(?,?,?,?,?,?,?)",
                                               (eq["id"], ed_maint_hm, str(datetime.now().date()), "Preventivo Base", user["id"], "Mantenimiento base inicial", utc_now()))
                                
                                db.commit()
                                st.success("¡Datos del equipo y bases de horómetros actualizados correctamente!")
                                st.rerun()
                        
                        st.write(f"- ⏱️ **Horómetro Actual:** `{st_info['current'] if st_info['current'] is not None else 'Sin datos'} hrs`")
                        st.write(f"- 🛠️ **Último Mantenimiento:** `{st_info['service_hm'] if st_info['service_hm'] is not None else 'Sin registro'} hrs`")
                        st.write(f"- ⏳ **Faltan para mant.:** `{st_info['remaining'] if st_info['remaining'] is not None else 'N/D'} hrs`")
                        
                        if st.button("Eliminar Equipo", key=f"del_eq_{eq['id']}"):
                            db.execute("DELETE FROM equipment WHERE id=?", (eq["id"],))
                            db.commit()
                            st.success(f"Equipo {eq['code']} eliminado.")
                            st.rerun()

        with tab3:
            st.subheader("Gestión de Usuarios y Técnicos")
            plants = db.execute("SELECT id, name FROM plants WHERE active=1 ORDER BY name").fetchall()
            
            with st.form("user_form"):
                u_username = st.text_input("Nombre de Usuario (Login)").strip().upper()
                u_display = st.text_input("Nombre Completo").strip()
                u_pass = st.text_input("Contraseña (mínimo 12 caracteres)", type="password")
                u_role = st.selectbox("Rol", ["tech", "admin"])
                
                st.markdown("**Asignar Proyectos Permitidos (para técnicos):**")
                selected_plant_ids = []
                for p in plants:
                    if st.checkbox(f"Proyecto: {p['name']}", key=f"p_check_{p['id']}"):
                        selected_plant_ids.append(p["id"])
                        
                u_submit = st.form_submit_button("Crear Usuario")
                
                if u_submit:
                    if not u_username or not u_display or len(u_pass) < 12:
                        st.error("Completa todos los campos. La contraseña debe tener al menos 12 caracteres.")
                    elif not u_username.replace("_", "").isalnum():
                        st.error("El usuario solo puede contener letras, números y guion bajo.")
                    else:
                        try:
                            cur = db.execute(
                                "INSERT INTO users(username, display_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                                (u_username, u_display, generate_password_hash(u_pass), u_role, utc_now())
                            )
                            new_uid = cur.lastrowid
                            if u_role == "tech":
                                for pid in selected_plant_ids:
                                    db.execute("INSERT OR IGNORE INTO user_plants(user_id, plant_id) VALUES(?,?)", (new_uid, pid))
                            db.commit()
                            st.success("¡Usuario creado con éxito!")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Ese nombre de usuario ya existe.")
                            
            st.markdown("### Usuarios Existentes y Edición")
            users_list = db.execute("SELECT * FROM users ORDER BY role, username").fetchall()
            for u in users_list:
                assigned_p_rows = db.execute("SELECT plant_id FROM user_plants WHERE user_id=?", (u["id"],)).fetchall()
                current_assigned_ids = [r["plant_id"] for r in assigned_p_rows]
                
                with st.expander(f"👤 {u['username']} ({u['display_name']}) - Rol: {u['role']}"):
                    with st.form(f"edit_user_{u['id']}"):
                        ed_disp = st.text_input("Nombre Completo", value=u["display_name"], key=f"udisp_{u['id']}")
                        role_list = ["tech", "admin"]
                        r_idx = role_list.index(u["role"]) if u["role"] in role_list else 0
                        ed_role = st.selectbox("Rol", role_list, index=r_idx, key=f"urole_{u['id']}")
                        
                        st.markdown("**Editar Proyectos Asignados:**")
                        new_assigned = []
                        for p in plants:
                            is_checked = p["id"] in current_assigned_ids
                            if st.checkbox(f"Proyecto: {p['name']}", value=is_checked, key=f"up_chk_{u['id']}_{p['id']}"):
                                new_assigned.append(p["id"])
                                
                        up_u = st.form_submit_button("Actualizar Usuario")
                        if up_u:
                            db.execute("UPDATE users SET display_name=?, role=? WHERE id=?", (ed_disp, ed_role, u["id"]))
                            db.execute("DELETE FROM user_plants WHERE user_id=?", (u["id"],))
                            if ed_role == "tech":
                                for pid in new_assigned:
                                    db.execute("INSERT OR IGNORE INTO user_plants(user_id, plant_id) VALUES(?,?)", (u["id"], pid))
                            db.commit()
                            st.success("¡Usuario actualizado correctamente!")
                            st.rerun()
                            
                    if u["id"] != user["id"]:
                        if st.button("Eliminar Usuario", key=f"del_user_{u['id']}"):
                            db.execute("DELETE FROM users WHERE id=?", (u["id"],))
                            db.commit()
                            st.success(f"Usuario {u['username']} eliminado.")
                            st.rerun()
                    else:
                        st.text("(Tu cuenta actual)")

    db.close()