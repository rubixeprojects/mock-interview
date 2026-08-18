# -*- coding: utf-8 -*-
import os
import time
import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(page_title="Internship Portal Admin", layout="wide")

BASE_URL = os.getenv("DJANGO_API_BASE", "http://127.0.0.1:8001/portal/api")
CC_URL = f"{BASE_URL}/control-center"

if "http_session" not in st.session_state:
    st.session_state["http_session"] = requests.Session()

def login_admin(email, password):
    try:
        resp = st.session_state["http_session"].post(f"{CC_URL}/login/", json={"email": email, "password": password})
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                st.session_state["admin"] = data["admin"]
                return True, "Login successful!"
        return False, f"Login failed: {resp.json().get('error', 'Invalid credentials')}"
    except Exception as e:
        return False, f"Connection error: {e}"

def logout_admin():
    try:
        st.session_state["http_session"].post(f"{CC_URL}/logout/")
    except: pass
    st.session_state["admin"] = None
    st.session_state["http_session"] = requests.Session()
    st.rerun()

def get_auth_status():
    try:
        resp = st.session_state["http_session"].get(f"{CC_URL}/status/")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("authenticated"):
                st.session_state["admin"] = data["admin"]
                return True
        st.session_state["admin"] = None
        return False
    except:
        return False

if "admin" not in st.session_state:
    st.session_state["admin"] = None
    get_auth_status()

if st.session_state["admin"] is None:
    st.title("🎓 Internship Portal Admin")
    st.subheader("🔒 Admin Login")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Login", width="stretch"):
            success, msg = login_admin(email, password)
            if success:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
    st.stop()

# Navigation
NAV_OPTIONS = [
    "🏠 Home Dashboard", "👥 Students", "📧 Bulk Actions", "📄 CSV & Data", "🤝 Teams & Projects", "📊 Evaluations", "🕐 Cron Jobs"
]
if st.session_state["admin"].get("role") == "SuperAdmin":
    NAV_OPTIONS.append("⚙️ Control Center")

with st.sidebar:
    st.title("🎓 Admin Portal")
    if st.session_state["admin"]:
        st.write(f"👤 **{st.session_state['admin']['name']}**")
        st.caption(f"{st.session_state['admin']['role']}")
        if st.button("Logout", key="logout_btn"):
            logout_admin()
    st.divider()

    # Resolve any pending navigation BEFORE the radio widget is instantiated
    if "_nav_pending" in st.session_state:
        st.session_state["nav_selection"] = st.session_state.pop("_nav_pending")

    selected_nav = st.radio("Navigation", NAV_OPTIONS, key="nav_selection")

st.title(f"🎓 {selected_nav}")

# Map selections to variable names for compatibility
tab_home = selected_nav if selected_nav == "🏠 Home Dashboard" else None
tab_students = selected_nav if selected_nav == "👥 Students" else None
tab_bulk = selected_nav if selected_nav == "📧 Bulk Actions" else None
tab_csv = selected_nav if selected_nav == "📄 CSV & Data" else None
tab_teams_projects = selected_nav if selected_nav == "🤝 Teams & Projects" else None
tab_evaluations = selected_nav if selected_nav == "📊 Evaluations" else None
tab_cron = selected_nav if selected_nav == "🕐 Cron Jobs" else None
tab_cc = selected_nav if selected_nav == "⚙️ Control Center" else None

PIPELINE_STATUSES = [
    "Registered", "TeamIDGiven", "CapStoneProjectsAssigned",
    "ReadyForClientPick", "ClientProjectAssigned", "AllCompleted",
    # AIE two-phase statuses
    "CDSCycleComplete", "AIECapstoneAssigned", "AIEReadyForClientPick", "AIEClientAssigned",
    # Archived
    "LegacyArchived",
]
STATUS_ICONS = {
    "Registered": "🟡", "TeamIDGiven": "🔵", "CapStoneProjectsAssigned": "🟣",
    "ReadyForClientPick": "🟠", "ClientProjectAssigned": "🔴", "AllCompleted": "✅",
    "CDSCycleComplete": "🏁", "AIECapstoneAssigned": "🟣", "AIEReadyForClientPick": "🟠",
    "LegacyArchived": "🗄️",
    "AIEClientAssigned": "🔴",
}

@st.cache_data(ttl=60)
def fetch_all_student_emails():
    try:
        r = st.session_state["http_session"].get(f"{BASE_URL}/all-student-emails/", timeout=15)
        return r.json().get("emails", [])
    except Exception:
        return []

def refresh_csv_logs():
    try:
        hr = st.session_state["http_session"].get(f"{BASE_URL}/list-csv-logs/", timeout=15)
        if hr.status_code == 200:
            st.session_state["csv_logs"] = hr.json().get("logs", [])
    except: pass

if "csv_logs" not in st.session_state:
    st.session_state["csv_logs"] = []
    refresh_csv_logs()

def save_update(payload):
    try:
        r = st.session_state["http_session"].patch(f"{BASE_URL}/student-update/", json=payload, timeout=15)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

def save_team_update(payload):
    try:
        r = st.session_state["http_session"].patch(f"{BASE_URL}/team-update/", json=payload, timeout=15)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

def _api_get_cc(url, params=None):
    """GET against the Control Center sub-URL (CC_URL)."""
    try:
        r = st.session_state["http_session"].get(f"{CC_URL}/{url}", params=params, timeout=15)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

def _api_post_cc(url, payload):
    """POST against the Control Center sub-URL (CC_URL)."""
    try:
        r = st.session_state["http_session"].post(f"{CC_URL}/{url}", json=payload, timeout=15)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

def _api_delete(url, payload):
    try:
        r = st.session_state["http_session"].request("DELETE", f"{BASE_URL}/{url}", json=payload, timeout=15)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

def _api_post(url, payload):
    try:
        r = st.session_state["http_session"].post(f"{BASE_URL}/{url}", json=payload, timeout=30)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

def _api_get(url, params=None):
    try:
        r = st.session_state["http_session"].get(f"{BASE_URL}/{url}", params=params, timeout=15)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

def fetch_students_table(course, status, has_team, q, page=1, page_size=20):
    params = {
        "page": page,
        "page_size": page_size,
        "q": q,
        "course": course if course != "All" else "",
        "status": status if status != "All" else "",
        "has_team": has_team.lower() if has_team not in ["All", "Any"] else ""
    }
    return _api_get("students-table/", params=params)

# ---------------------------------------------------------------------------
# Cached wrappers for render-triggered calls (fire on every Streamlit rerun).
# _bust is passed as the first arg and holds st.session_state["cache_bust"].
# Leading underscore tells @st.cache_data not to hash it, but changing its
# value (after a write action) forces a cache miss on the next rerun.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=120, show_spinner=False)
def _cached_pipeline_stats(_bust):
    """Cached pipeline stats — 2 min TTL, busted after write operations."""
    try:
        r = st.session_state["http_session"].get(f"{BASE_URL}/pipeline-stats/", timeout=15)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

@st.cache_data(ttl=60, show_spinner=False)
def _cached_students_table(_bust, course, status, has_team, q, page, page_size):
    """Cached students table — 1 min TTL for filter-only queries."""
    params = {
        "page": page,
        "page_size": page_size,
        "q": q,
        "course": course if course != "All" else "",
        "status": status if status != "All" else "",
        "has_team": has_team.lower() if has_team not in ["All", "Any"] else "",
    }
    try:
        r = st.session_state["http_session"].get(f"{BASE_URL}/students-table/", params=params, timeout=15)
        return r.json()
    except Exception as ex:
        return {"success": False, "error": str(ex)}

def _bust_value():
    """Return the current cache bust token from session state."""
    return st.session_state.get("cache_bust", 0)

def _trigger_cache_bust():
    """Call after any write action to force a fresh fetch on next rerun."""
    st.session_state["cache_bust"] = time.time()

# ============================================================================
# TAB 1: HOME DASHBOARD (Direct Action & Overview)
# ============================================================================
if tab_home:
    # Show commit banner if we just navigated here from a CSV push
    _cr = st.session_state.get("commit_result")
    if _cr:
        _cr_res = _cr.get("response", {})
        st.success(
            f"✅ CSV synced — "
            f"**{_cr_res.get('new_enrollments_count', 0)}** new enrollments, "
            f"**{_cr_res.get('new_identities_count', 0)}** new identities, "
            f"**{_cr_res.get('modified_count', 0)}** updated  |  "
            f"{_cr['filename']} at {_cr['ran_at']}"
        )

    # Show team action result if we just navigated here from team formation
    _tar = st.session_state.get("team_action_result")
    if _tar:
        st.success(f"✅ {_tar['action']} — **{_tar['team_id']}** | Course: {_tar['course']} | {_tar['ran_at']}")
        st.write("**Members:**")
        for m in _tar["members"]:
            st.markdown(f"- {m}")
        if st.button("✖ Dismiss", key="dismiss_team_result"):
            st.session_state.pop("team_action_result", None)
            st.rerun()

    st.subheader("📊 Global Pipeline Overview")

    # 1. Pipeline Stats (Cards) — cached, busted after write actions
    stats = _cached_pipeline_stats(_bust_value())
    if stats and stats.get("success") is not False:
        # status_counts is a list of {"status": "...", "count": N}
        sc_map = {row["status"]: row["count"] for row in stats.get("status_counts", [])}
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Identities", f"{stats.get('total_identities', 0):,}")
        c2.metric("Enrollments", f"{stats.get('total_enrollments', 0):,}")
        c3.metric("Registered", sc_map.get("Registered", 0))
        c4.metric("TeamIDGiven", sc_map.get("TeamIDGiven", 0))
        c5.metric("ProjectsAssigned", sc_map.get("CapStoneProjectsAssigned", 0))
        c6.metric("Completed", sc_map.get("AllCompleted", 0))
    else:
        st.warning("Could not load pipeline statistics.")
        
    st.divider()

    # 2. Student Search & Filter Table
    st.write("### 🔎 Student Search & Quick Actions")
    
    col_f1, col_f2, col_f3, col_f4 = st.columns([2, 1, 1, 1])
    q = col_f1.text_input("Search Name/Email/TeamID", placeholder="e.g. john@example.com", key="home_search_input")
    course_f = col_f2.selectbox("CourseFilter", ["All", "CDS", "AIE", "CDE", "CDA"], key="home_course_select")
    status_f = col_f3.selectbox("StatusFilter", ["All"] + PIPELINE_STATUSES, key="home_status_select")
    has_team_f = col_f4.selectbox("HasTeamFilter", ["Any", "Yes", "No"], key="home_team_select")

    # Pagination state
    if "home_page" not in st.session_state: st.session_state.home_page = 1

    res = _cached_students_table(
        _bust_value(),
        course_f, status_f, has_team_f, q,
        st.session_state.home_page, 20
    )
    if res and res.get("error"):
        st.info("The paginated student table API (`/students-table/`) is not yet implemented. Use the **Students** tab -> Search Specific to look up individual students.")
        res = None
    if res and res.get("success") is not False and res.get("results"):
        students = res.get("results", [])
        total_count = res.get("total_count", len(students))
        
        df = pd.DataFrame(students)

        df["Status"] = df["status"].apply(lambda s: f"{STATUS_ICONS.get(s, '❓')} {s}")
        df["Days"] = df["days_in_status"].apply(lambda d: f"{d} days" if d is not None else "—")

        # Reorder columns — plain email (no href links to avoid full page reload)
        cols = ['name', 'email', 'course', 'Status', 'team_id', 'Days']
        df_display = df[cols].rename(columns={"team_id": "Team ID", "email": "Email", "name": "Name", "course": "Course"})

        st.dataframe(df_display, width="stretch", hide_index=True)

        # Pagination Buttons
        p1, p2, p3 = st.columns([1, 2, 1])
        if p1.button("⬅️ Previous", disabled=(st.session_state.home_page <= 1), key="home_prev"):
            st.session_state.home_page -= 1
            st.rerun()
        p2.write(f"Page {st.session_state.home_page} of {(total_count // 20) + 1} ({total_count} total)")
        if p3.button("Next ➡️", disabled=(not res.get("has_next")), key="home_next"):
            st.session_state.home_page += 1
            st.rerun()

        st.divider()
        st.write("**View Student Details** — enter an email from the table above:")
        nav_col1, nav_col2 = st.columns([3, 1])
        nav_email = nav_col1.text_input("Student email", placeholder="student@example.com", label_visibility="collapsed", key="home_nav_email")
        if nav_col2.button("Go to Student", key="home_nav_btn"):
            if nav_email.strip():
                st.session_state["student_prefill"] = nav_email.strip()
                st.session_state["nav_selection"] = "👥 Students"
                st.rerun()
    else:
        st.info("No students found matching filters.")

    # --- Previous CSV Push Runs ---
    st.divider()
    st.write("### 🕑 Previous Runs")

    _commit = st.session_state.get("commit_result")
    if _commit:
        _res = _commit.get("response", {})
        _cols = st.columns(4)
        _cols[0].metric("New Enrollments", _res.get("new_enrollments_count", 0))
        _cols[1].metric("New Identities", _res.get("new_identities_count", 0))
        _cols[2].metric("Updated", _res.get("updated_count", 0))
        _cols[3].metric("Skipped", _res.get("skipped_count", 0))
        st.caption(f"Last push: **{_commit['filename']}** at {_commit['ran_at']}")
        with st.expander("📋 Full Response JSON", expanded=False):
            st.json(_res)
        if st.button("✖ Clear last result", key="clear_commit_result"):
            st.session_state.pop("commit_result", None)
            st.rerun()
    else:
        st.caption("No push run this session. Results will appear here after you sync a CSV.")

    _logs = st.session_state.get("csv_logs", [])
    if _logs:
        st.write("**Recent Upload Batches** (from server history)")
        for _log in _logs[:5]:
            st.markdown(f"- **Batch {_log['id']}** | {_log['timestamp']} — {_log['summary']}")
        if len(_logs) > 5:
            st.caption(f"… and {len(_logs) - 5} more. See **CSV & Data → DB Health & History** for full list.")

    # --- Pipeline Status Glossary (Always visible for all admins) ---
    st.divider()
    with st.expander("📖 View Pipeline Status Glossary", expanded=False):
        st.markdown("""
### PIPELINE STATUS GLOSSARY

🟡 **Registered**  
Student has enrolled but has not yet been assigned a team.

🔵 **TeamIDGiven**  
Student has been assigned a unique Team ID.  
*Admin action:* Go to student card -> Assign Project (PRAICP-xxx -> assigned)

🟣 **CapStoneProjectsAssigned**  
One or more capstone projects are assigned to the team.  
*Admin action:* Monitor submission queue in Evaluations tab.

🟠 **ReadyForClientPick**  
All capstone projects have been evaluated.  
*Admin action:* Verify via student card, assign client project if needed.

🔴 **ClientProjectAssigned**  
Client project assigned to the team.  
*Admin action:* Monitor client project submissions.

✅ **AllCompleted**  
All projects (capstone + client) submitted and evaluated.  
*Admin action:* Issue NOC via student card or Bulk NOC in Bulk Actions tab.

---
**COURSE CAPSTONE REQUIREMENTS**  
CDS — 4 capstones | AIE — 3 capstones | CDE — 2 capstones | CDA — 2 capstones

---
**TEAM PREFERENCE TYPES**  
`individual` — Student wants a solo team (1 person)  
`assign_team` — Student wants the system to auto-assign them to a group of 4

---
**TEAMPROJECT STATUS**  
`assigned` — Project assigned, submission not yet made  
`submitted` — Student submitted files, evaluation in progress  
`evaluated` — Evaluation complete, grade assigned

---
**EVALUATION VISIBILITY RULES**  
- Evaluations are hidden from students for 2 days after creation (`visibility_after`)  
- Email sent only after: `reviewed=True` AND `visibility_after <= now` AND `email_sent=False`  
- Trigger manually via "Trigger Pending Emails" in the Evaluations tab
        """)

# ============================================================================
# TAB 2: STUDENTS (Enhanced Student Search)
# ============================================================================
if tab_students:
    st.subheader("Student Management")

    # If navigated from Home tab with a prefill, switch to Search Specific mode
    if st.session_state.get("student_prefill"):
        st.session_state["students_mode"] = "Search Specific"

    # Browse mode vs Search mode toggle
    browse_mode = st.radio("Mode", ["Browse All", "Search Specific"], horizontal=True, key="students_mode")

    if browse_mode == "Browse All":
        # Reuse the same filters as home dashboard
        if "students_filters" not in st.session_state:
            st.session_state["students_filters"] = {
                "course": "All",
                "status": "All",
                "has_team": "All",
                "search": ""
            }

        # Filters
        filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)

        with filter_col1:
            course_options = ["All", "CDS", "AIE", "CDE", "CDA"]
            selected_course = st.selectbox("Course", course_options,
                                         index=course_options.index(st.session_state["students_filters"]["course"]),
                                         key="students_course_filter")

        with filter_col2:
            status_options = ["All"] + PIPELINE_STATUSES
            selected_status = st.selectbox("Status", status_options,
                                         index=status_options.index(st.session_state["students_filters"]["status"]),
                                         key="students_status_filter")

        with filter_col3:
            team_options = ["All", "Yes", "No"]
            selected_has_team = st.selectbox("Has Team", team_options,
                                           index=team_options.index(st.session_state["students_filters"]["has_team"]),
                                           key="students_team_filter")

        with filter_col4:
            search_query = st.text_input("Search", value=st.session_state["students_filters"]["search"],
                                       key="students_search_filter")

        # Update filters
        st.session_state["students_filters"] = {
            "course": selected_course,
            "status": selected_status,
            "has_team": selected_has_team,
            "search": search_query
        }

        # Fetch and display students (similar to home tab but with full student cards)
        table_data = fetch_students_table(
            selected_course, selected_status, selected_has_team, search_query
        )
        if table_data and table_data.get("error"):
            st.info("Paginated browse API not yet available. Use **Search Specific** mode to look up students.")
            table_data = None

        browse_results = table_data.get("results", []) if table_data else []
        if browse_results:
            st.write(f"Found {table_data.get('total_count', len(browse_results))} students")

            # Show first 10 as expandable cards
            for student_data in browse_results[:10]:
                with st.expander(f"{student_data['name']} — {student_data['email']}"):
                    st.write(f"**Course:** {student_data['course']}")
                    st.write(f"**Status:** {STATUS_ICONS.get(student_data['status'], '❓')} {student_data['status']}")
                    st.write(f"**Team ID:** {student_data.get('team_id') or 'None'}")
                    st.write(f"**Days in Status:** {student_data.get('days_in_status') or 'Unknown'}")

                    # Quick actions
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        if st.button("✏️ Edit", key=f"edit_{student_data['id']}"):
                            st.info("Edit functionality coming soon")
                    with col2:
                        if st.button("✉ Email", key=f"email_{student_data['id']}"):
                            st.info("Email functionality coming soon")
                    with col3:
                        if st.button("-> Next Stage", key=f"advance_{student_data['id']}"):
                            st.info("Advance status functionality coming soon")
        else:
            st.info("No students found.")

    else:  # Search Specific
        # Pre-populate from Home tab "Go to Student" navigation.
        # Pop the prefill BEFORE rendering the text_input so session_state is set
        # before Streamlit reads the widget key for the first time this run.
        prefill = st.session_state.pop("student_prefill", None)
        if prefill:
            st.session_state["search_query_students"] = prefill
            st.session_state["search_results"] = []

        # Auto-refresh: after any write action, re-fetch results with the same query
        # so the form doesn't re-render with stale enrollment data.
        if st.session_state.pop("students_auto_refresh", False):
            q_val = st.session_state.get("search_query_students", "")
            if q_val:
                res = _api_get("student-lookup/", params={"q": q_val})
                if res and res.get("success"):
                    st.session_state["search_results"] = res.get("results", [])

        query = st.text_input("🔍 Enter name or email", key="search_query_students")

        if st.button("Search", key="search_btn_students"):
            if query:
                with st.spinner("Searching..."):
                    res = _api_get("student-lookup/", params={"q": query})
                if res and res.get("success"):
                    st.session_state["search_results"] = res.get("results", [])
                else:
                    st.session_state["search_results"] = []
                    st.error(res.get("error", "Search failed"))

        results = st.session_state.get("search_results", [])

        for idx, student in enumerate(results):
            with st.expander(f"👤 {student['name']} ({student['email']})"):
                c1, c2 = st.columns(2)
                with c1:
                    st.write("#### 🆔 Identity & Enrollments")
                    u_name = st.text_input("Name", value=student['name'], key=f"name_{idx}")
                    u_phone = st.text_input("Phone", value=student.get('phone', ''), key=f"phone_{idx}")
                    if st.button("💾 Save Name / Phone", key=f"save_identity_{idx}"):
                        id_payload = {"email": student['email']}
                        if u_name.strip() != student['name']: id_payload["name"] = u_name.strip()
                        if u_phone.strip() != (student.get('phone') or ''): id_payload["phone"] = u_phone.strip()
                        if len(id_payload) > 1:
                            r = save_update(id_payload)
                            if r.get("success"): st.success("Identity updated!"); _trigger_cache_bust(); st.session_state["students_auto_refresh"] = True; st.rerun()
                            else: st.error(r.get("error"))
                        else:
                            st.info("No changes to name or phone.")
                    u_noc = st.checkbox("NOC Issued", value=student.get('NOC_issued', False), key=f"noc_{idx}")
                    u_pass = st.text_input("New Password", type="password", placeholder="Leave blank to keep current", key=f"pass_{idx}")
                    
                    for en in student.get('enrollments', []):
                        st.divider()
                        st.write(f"**Course: {en['course']}**")
                        u_stat = st.selectbox("Status", PIPELINE_STATUSES, index=PIPELINE_STATUSES.index(en['status']), key=f"stat_{idx}_{en['course']}")

                        course_opts = ["CDS", "AIE", "CDE", "CDA"]
                        current_course_idx = course_opts.index(en['course']) if en['course'] in course_opts else 0
                        u_new_course = st.selectbox("Transfer to Course", course_opts, index=current_course_idx, key=f"nc_{idx}_{en['course']}")

                        team_info = en.get('team') or {}
                        current_team_id = team_info.get('team_id') or ""
                        u_team_id = st.text_input(
                            "Team ID",
                            value=current_team_id,
                            placeholder="e.g. PTID-CDS-MAR-26-11122",
                            key=f"tid_{idx}_{en['course']}",
                        )

                        # ── Pending team-assign confirmation ──────────────────
                        pending_key = f"pending_team_assign_{idx}_{en['course']}"
                        if pending_key in st.session_state:
                            pending = st.session_state[pending_key]
                            st.warning(f"⚠️ Team already exists — confirm before adding")
                            st.caption(pending["message"])
                            existing = pending.get("existing_members", [])
                            if existing:
                                st.caption("Current members: " + " · ".join(existing))
                            c_yes, c_no = st.columns(2)
                            if c_yes.button("✅ Yes, add to existing team", key=f"confirm_team_{idx}_{en['course']}"):
                                r2 = save_update(pending["payload"])
                                del st.session_state[pending_key]
                                if r2.get("success"):
                                    if r2.get("new_team_id"):
                                        st.success(f"✅ {student['email']} added to {r2['new_team_id']} — team now has {r2['team_member_count']} member(s). No other students were affected.")
                                    else:
                                        st.success(f"✅ Updated {student['email']} only.")
                                    _trigger_cache_bust(); st.session_state["students_auto_refresh"] = True; st.rerun()
                                else: st.error(r2.get("error"))
                            if c_no.button("❌ Cancel", key=f"cancel_team_{idx}_{en['course']}"):
                                del st.session_state[pending_key]
                                st.rerun()

                        if st.button("Save Changes", key=f"save_{idx}_{en['course']}"):
                            # Only send enrollment/status fields here.
                            # Name and phone have their own Save Identity button below.
                            payload = {
                                "email": student['email'],
                                "course": en['course'],
                                "status": u_stat,
                            }
                            if u_noc != student.get('NOC_issued', False):
                                payload["NOC_issued"] = u_noc
                            if u_pass:
                                payload["password"] = u_pass
                            if u_new_course != en['course']:
                                payload["new_course"] = u_new_course
                            if u_team_id.strip() != current_team_id:
                                payload["team_id"] = u_team_id.strip()

                            r = save_update(payload)
                            if r.get("requires_confirmation"):
                                st.session_state[pending_key] = {
                                    "payload": {**payload, "force_team_assign": True},
                                    "message": r["message"],
                                    "existing_members": r.get("existing_members", []),
                                }
                                st.rerun()
                            elif r.get("success"):
                                if r.get("new_team_id"):
                                    st.success(f"✅ {student['email']} moved to {r['new_team_id']} — team now has {r['team_member_count']} member(s). No other students were affected.")
                                else:
                                    st.success(f"✅ Updated {student['email']} only.")
                                _trigger_cache_bust(); st.session_state["students_auto_refresh"] = True; st.rerun()
                            else: st.error(r.get("error"))

                        if not current_team_id:
                            if st.button("Create Solo Team", key=f"ct_{idx}_{en['course']}"):
                                r = _api_post("admin-create-individual-team/", {"email": student['email'], "course": en['course']})
                                if r.get("success"): st.success(f"Team {r['team_id']} created!"); _trigger_cache_bust(); st.session_state["students_auto_refresh"] = True; st.rerun()
                                else: st.error(r.get("error"))

                with c2:
                    st.write("#### 📊 Evaluations")
                    evals = student.get('evaluations', [])
                    if evals:
                        for ev in evals:
                            ev_key = f"ev_{ev['evaluation_id']}"
                            with st.container(border=True):
                                grade_opts = ["(none)", "A", "B", "C", "D"]
                                cur_grade = ev.get("grade") or "(none)"
                                grade_idx = grade_opts.index(cur_grade) if cur_grade in grade_opts else 0
                                st.write(f"**{ev['project_id']}** | {ev.get('course','?')} | {'✅ Sent' if ev.get('email_sent') else '📭 Unsent'}")
                                c_ev1, c_ev2 = st.columns(2)
                                new_grade = c_ev1.selectbox("Grade", grade_opts, index=grade_idx, key=f"grade_{ev_key}")
                                is_rev = c_ev2.toggle("Reviewed", value=bool(ev.get("reviewed")), key=f"rev_{ev_key}")

                                if st.button("📧 Resend Email", key=f"send_{ev_key}"):
                                    res = _api_post("resend-evaluation-email/", {"evaluation_id": ev["evaluation_id"]})
                                    if res.get("success"): st.success("Email resent!"); _trigger_cache_bust()
                                    else: st.error(res.get("error", "Failed"))
                    else:
                        st.info("No evaluations yet.")
                    # --- Team Formation (per enrollment, shown per course) ---
                    for enr in student.get("enrollments", []):
                        enr_course = enr.get("course", "")
                        enr_team = enr.get("team") or {}
                        enr_team_id = enr_team.get("team_id")
                        if enr_team_id:
                            continue  # already has a team — handled in identity section above
                        st.divider()
                        st.write(f"#### 🤝 Team Formation — {enr_course}")
                        st.info("No team assigned yet")
                        col_tf1, col_tf2 = st.columns(2)
                        if col_tf1.button("Create Solo Team", key=f"solo_{idx}_{enr_course}"):
                            res = _api_post("admin-create-individual-team/", {"email": student['email'], "course": enr_course})
                            if res.get("success"):
                                from datetime import datetime as _dt
                                st.session_state["team_action_result"] = {
                                    "action": "Solo Team Created",
                                    "team_id": res["team_id"],
                                    "members": [student['email']],
                                    "course": enr_course,
                                    "ran_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                                }
                                _trigger_cache_bust()
                                st.session_state["_nav_pending"] = "🏠 Home Dashboard"
                                st.rerun()
                            else: st.error(res.get("error", "Failed"))

                        if col_tf2.button("Form Group...", key=f"group_btn_{idx}_{enr_course}"):
                            st.session_state[f"show_group_picker_{idx}_{enr_course}"] = True

                        if st.session_state.get(f"show_group_picker_{idx}_{enr_course}"):
                            st.caption(f"Select up to 3 students who want group assignment in {enr_course}:")
                            un_res = _api_get("unassigned-students/", params={"course": enr_course}) or {}
                            others = [s for s in un_res.get("students", []) if s["email"] != student["email"]]
                            if others:
                                opts = {f"{s['name']} ({s['email']})": s["email"] for s in others}
                                picks = st.multiselect("Teammates", list(opts.keys()), max_selections=3, key=f"pick_{idx}_{enr_course}")
                                gc1, gc2 = st.columns(2)
                                if gc1.button("Confirm Group", key=f"confirm_grp_{idx}_{enr_course}"):
                                    if not picks: st.warning("Pick at least one teammate")
                                    else:
                                        emails = [student['email']] + [opts[p] for p in picks]
                                        res = _api_post("admin-form-group-team/", {"emails": emails, "course": enr_course})
                                        if res.get("success"):
                                            from datetime import datetime as _dt
                                            st.session_state["team_action_result"] = {
                                                "action": "Group Team Created",
                                                "team_id": res["team_id"],
                                                "members": emails,
                                                "course": enr_course,
                                                "ran_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            }
                                            st.session_state.pop(f"show_group_picker_{idx}_{enr_course}", None)
                                            _trigger_cache_bust()
                                            st.session_state["_nav_pending"] = "🏠 Home Dashboard"
                                            st.rerun()
                                        else: st.error(res.get("error", "Failed"))
                                if gc2.button("Cancel", key=f"cancel_grp_{idx}_{enr_course}"):
                                    st.session_state.pop(f"show_group_picker_{idx}_{enr_course}", None)
                                    st.rerun()
                            else:
                                st.warning(f"No other students in {enr_course} have requested group assignment yet.")
                                if st.button("Cancel", key=f"cancel_grp2_{idx}_{enr_course}"):
                                    st.session_state.pop(f"show_group_picker_{idx}_{enr_course}", None)
                                    st.rerun()

                # ── Admin Temp Access ─────────────────────────────────────
                st.divider()
                st.markdown("**🔑 Admin Temp Access**")
                temp_key = f"temp_access_{idx}"
                if st.button("Generate Temp Login Password", key=f"gen_temp_{idx}"):
                    try:
                        r = st.session_state["http_session"].post(
                            f"{BASE_URL}/generate-temp-access/",
                            json={"email": student['email']},
                            timeout=15,
                        )
                        result = r.json()
                        if result.get("success"):
                            st.session_state[temp_key] = {
                                "password": result["temp_password"],
                                "expires_in": result["expires_in_minutes"],
                            }
                        else:
                            st.error(result.get("error", "Failed to generate temp access"))
                    except Exception as ex:
                        st.error(f"Connection error: {ex}")

                if st.session_state.get(temp_key):
                    _td = st.session_state[temp_key]
                    with st.expander("Temp Password — valid once, expires in 1 hour", expanded=True):
                        st.code(_td["password"], language=None)
                        st.caption(
                            f"Go to the student portal, enter **{student['email']}** as the email, "
                            f"and use this password to log in. Single-use · expires in {_td['expires_in']} min."
                        )
                    if st.button("Clear", key=f"clear_temp_{idx}"):
                        st.session_state.pop(temp_key, None)
                        st.rerun()


# ============================================================================
# TAB 3: BULK ACTIONS
# ============================================================================
if tab_bulk:
    st.subheader("🚀 Bulk Administrative Actions")
    bt1, bt2, bt3 = st.tabs(["📧 Bulk Email", "🔄 Bulk Status", "📑 Bulk NOC/Password"])

    with bt1:
        st.write("### Generic Bulk Email")
        b_course = st.selectbox("Target Course", ["All", "CDS", "AIE", "CDE", "CDA"], key="be_course")
        b_status = st.selectbox("Target Status", ["All"] + PIPELINE_STATUSES, key="be_status")
        
        b_subject = st.text_input("Subject", key="be_subject")
        b_msg = st.text_area("Message Template", height=150, help="Use {{name}}, {{course}}, {{team_id}}", key="be_msg")
        
        if st.button("Send Bulk Email", type="primary", key="be_send"):
            payload = {
                "target": {"course": b_course if b_course != "All" else None, "status": b_status if b_status != "All" else None},
                "subject": b_subject,
                "message_template": b_msg
            }
            res = _api_post("bulk-email/", payload)
            if res.get("sent"): st.success(f"Sent {res['sent']} emails!")
            if res.get("failed"): st.error(f"Failed {len(res['failed'])} emails.")

    with bt2:
        st.write("### Bulk Status Transition")
        bs_course = st.selectbox("Course Context", ["All", "CDS", "AIE", "CDE", "CDA"], key="bs_course")
        bs_from = st.selectbox("From Status", PIPELINE_STATUSES, key="bs_from")
        bs_to = st.selectbox("To Status", PIPELINE_STATUSES, key="bs_to")
        
        if st.button("Execute Bulk Transition", type="primary", key="bs_execute"):
            payload = {"from_status": bs_from, "to_status": bs_to, "course": bs_course}
            res = _api_post("bulk-status-update/", payload)
            if res.get("success"):
                st.success(f"Updated {res['updated']} students!")
                _trigger_cache_bust()
                if res.get("skipped"):
                    with st.expander("Skipped Students"):
                        st.json(res["skipped"])

    with bt3:
        st.write("### Bulk NOC & Credentials")
        col_n1, col_n2 = st.columns(2)
        with col_n1:
            st.write("#### Issue NOCs")
            bn_course = st.selectbox("Course Context", ["All", "CDS", "AIE", "CDE", "CDA"], key="bn_course")
            if st.button("Issue NOCs for Completed", key="bn_go"):
                res = _api_post("bulk-noc/", {"course": bn_course})
                st.success(f"Issued {res.get('updated', 0)} NOCs!")
        
        with col_n2:
            st.write("#### Bulk Password Reset")
            bp_emails = st.text_area("Emails (one per line)", key="bp_emails")
            bp_pass = st.text_input("Manual Password (omit for random)", type="password", key="bp_pass")
            if st.button("Reset Passwords", key="bp_go"):
                emails = [e.strip() for e in bp_emails.split("\n") if e.strip()]
                res = _api_post("bulk-password-reset/", {"emails": emails, "new_password": bp_pass if bp_pass else None})
                st.json(res.get("results", []))

# ============================================================================
# TAB 4: CSV & DATA (Cleaned Up)
# ============================================================================
if tab_csv:
    st.subheader("CSV Upload & Data Management")

    csv_tabs = st.tabs(["📄 CSV Upload", "📊 Export Data", "⚠️ DB Health"])

    # Subtab: CSV Upload (existing functionality)
    with csv_tabs[0]:
        st.subheader("CSV Student Upload")

        with st.expander("ℹ️ How it works"):
            st.markdown("""
            1. Upload CSV → run **Dry Run** to preview all changes
            2. **Review** inconsistencies and errors — **Allow** or **Ignore** each one
            3. Critical entries (team assigned) show their full team roster
            4. Click **Push to Database** to commit allowed changes
            """)

        uploaded_file = st.file_uploader("Choose a CSV file", type=["csv"], key="csv_uploader_redesign")

        if uploaded_file:
            if st.button("🔍 Validate (Dry Run)", key="dry_run_btn_redesign"):
                with st.spinner("Analyzing CSV..."):
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")}
                    try:
                        r = st.session_state["http_session"].post(f"{BASE_URL}/upload-students-csv/?dry_run=true", files=files, timeout=30)
                        if r.status_code == 200:
                            result_data = r.json()
                            st.session_state["csv_dry_run_result"] = result_data
                            st.session_state["csv_file"] = uploaded_file.getvalue()
                            st.session_state["csv_filename"] = uploaded_file.name
                            # Default all decisions to "ignore"
                            st.session_state["inc_decisions"] = {
                                i: "ignore" for i in range(len(result_data.get("inconsistencies", [])))
                            }
                            st.session_state["err_decisions"] = {
                                i: "ignore" for i in range(len(result_data.get("errors", [])))
                            }
                        else:
                            st.error(f"Error: {r.text}")
                    except Exception as e:
                        st.error(f"Failed to connect: {e}")

        result = st.session_state.get("csv_dry_run_result")
        if result:
            st.divider()

            # ── Row counts ─────────────────────────────────────────────────
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Rows", result.get("total_rows", 0))
            c2.metric("New Identities", result.get("new_identities_count", 0))
            c3.metric("New Enrollments", result.get("new_enrollments_count", 0))
            c4.metric("Existing", result.get("existing_enrollments_count", 0))

            # ── Decision summary ────────────────────────────────────────────
            allowed_inc = [i for i, d in st.session_state.get("inc_decisions", {}).items() if d == "allow"]
            ignored_inc = [i for i, d in st.session_state.get("inc_decisions", {}).items() if d == "ignore"]
            allowed_err = [i for i, d in st.session_state.get("err_decisions", {}).items() if d == "allow"]
            ingest_new = True

            ds1, ds2, ds3, ds4 = st.columns(4)
            ds1.metric("Sync Valid Rows", result.get("new_enrollments_count", 0))
            ds2.metric("Allow Inconsistencies", len(allowed_inc))
            ds3.metric("Ignored", len(ignored_inc))
            ds4.metric("Allow Errors", len(allowed_err))

            commit_parts = []
            if ingest_new and result.get("new_enrollments_count", 0):
                commit_parts.append(f"**{result['new_enrollments_count']} new enrollments**")
            if allowed_inc:
                commit_parts.append(f"**{len(allowed_inc)} resolved inconsistencies**")
            if commit_parts:
                st.info(f"Ready to commit: {' and '.join(commit_parts)}")
            else:
                st.warning("No changes staged. Review inconsistencies below or upload a CSV with new rows.")

            # ── INCONSISTENCIES ─────────────────────────────────────────────
            inconsistencies = result.get("inconsistencies", [])
            st.subheader(f"⚠️ Inconsistencies ({len(inconsistencies)})")

            if inconsistencies:
                all_flagged_emails = [inc.get("email", "") for inc in inconsistencies]

                df_inc = pd.DataFrame(inconsistencies)
                severity_counts = df_inc["severity"].value_counts().to_dict() if "severity" in df_inc.columns else {}

                sb1, sb2, sb3, sb4 = st.columns(4)
                sb1.metric("🔴 Critical — Clean Teammates", severity_counts.get("critical_other", 0),
                    help="Has other team members who are NOT flagged. Changing course disrupts clean teammates.")
                sb2.metric("🟡 Critical — Same People", severity_counts.get("critical_same", 0),
                    help="Teammates are all also flagged.")
                sb3.metric("🟠 High", severity_counts.get("high", 0),
                    help="Course mismatch, solo/no team.")
                sb4.metric("🟢 Low", severity_counts.get("low", 0),
                    help="Minor issues like name casing.")

                SEV_LABELS = {
                    "critical_other": "🔴 CRITICAL — Clean teammates affected",
                    "critical_same":  "🟡 CRITICAL — All teammates also flagged",
                    "high":           "🟠 HIGH",
                    "low":            "🟢 LOW",
                }

                fc1, fc2 = st.columns(2)
                unique_sevs = sorted(df_inc["severity"].unique().tolist()) if "severity" in df_inc.columns else []
                sev_options_map = {SEV_LABELS.get(s, s): s for s in unique_sevs}
                sel_sev_label = fc1.selectbox("Filter by Severity", ["All"] + list(sev_options_map.keys()), key="inc_sev_rd")
                sel_sev = sev_options_map.get(sel_sev_label, "All") if sel_sev_label != "All" else "All"

                type_opts = ["All"] + sorted(df_inc["type"].unique().tolist()) if "type" in df_inc.columns else ["All"]
                sel_type = fc2.selectbox("Filter by Type", type_opts, key="inc_type_rd")

                visible_indices = [
                    i for i, inc in enumerate(inconsistencies)
                    if (sel_sev == "All" or inc.get("severity") == sel_sev)
                    and (sel_type == "All" or inc.get("type") == sel_type)
                ]
                st.caption(f"Showing {len(visible_indices)} of {len(inconsistencies)} inconsistencies")

                # Bulk action buttons
                ba1, ba2 = st.columns([1, 1])
                if ba1.button(f"✅ Allow All ({len(visible_indices)} shown)", key="bulk_allow_inc_rd"):
                    for i in visible_indices:
                        st.session_state["inc_decisions"][i] = "allow"
                    st.rerun()
                if ba2.button(f"🚫 Ignore All ({len(visible_indices)} shown)", key="bulk_ignore_inc_rd"):
                    for i in visible_indices:
                        st.session_state["inc_decisions"][i] = "ignore"
                    st.rerun()

                st.caption("• **Allow** = accept the CSV value and update the DB  •  **Ignore** = skip this row, keep DB as-is")

                for i, inc in enumerate(inconsistencies):
                    if sel_sev != "All" and inc.get("severity") != sel_sev:
                        continue
                    if sel_type != "All" and inc.get("type") != sel_type:
                        continue

                    severity = inc.get("severity", "low")
                    sev_badge = SEV_LABELS.get(severity, severity)

                    with st.container(border=True):
                        col_info, col_action = st.columns([4, 1])
                        with col_info:
                            st.markdown(f"**Row {inc.get('row')}** | {sev_badge} | `{inc.get('type')}`")
                            st.markdown(f"**Email:** {inc.get('email')}")
                            st.markdown(f"**Issue:** {inc.get('reason')}")
                            if inc.get("csv_value") and inc.get("db_value"):
                                st.markdown(f"CSV: `{inc.get('csv_value')}` → DB: `{inc.get('db_value')}`")
                        with col_action:
                            decision = st.radio(
                                "Action",
                                ["Allow", "Ignore"],
                                index=0 if st.session_state["inc_decisions"].get(i) == "allow" else 1,
                                key=f"inc_rd_{i}",
                                horizontal=False,
                            )
                            st.session_state["inc_decisions"][i] = decision.lower()

                        # Team impact panel for critical entries
                        if severity in ("critical_other", "critical_same"):
                            with st.expander("🔍 Team Impact"):
                                try:
                                    flagged_param = ",".join(all_flagged_emails)
                                    team_res = _api_get("team-roster/", params={
                                        "team_id": inc.get("team_id", ""),
                                        "flagged_emails": flagged_param,
                                    })
                                    if team_res and team_res.get("members"):
                                        flagged_members = [m for m in team_res["members"] if m.get("is_flagged")]
                                        clean_members = [m for m in team_res["members"] if not m.get("is_flagged")]
                                        if clean_members:
                                            st.warning(f"⚠️ {len(clean_members)} teammate(s) NOT flagged — allowing this change would disrupt them!")
                                            for m in clean_members:
                                                st.error(f"🟩 **{m['name']}** (`{m['email']}`)")
                                        if flagged_members:
                                            st.info(f"{len(flagged_members)} teammate(s) also flagged in this CSV:")
                                            for m in flagged_members:
                                                st.markdown(f"  ⚠️ **{m['name']}** (`{m['email']}`)")
                                except Exception as te:
                                    st.warning(f"Could not fetch team roster: {te}")
            else:
                st.success("✅ No inconsistencies!")

            # ── ERRORS ─────────────────────────────────────────────────────
            errors = result.get("errors", [])
            if errors:
                st.subheader(f"🚨 Structural Errors ({len(errors)})")
                st.caption("'Allow' attempts to skip gracefully; 'Ignore' excludes the row entirely.")

                # Bulk buttons for errors
                eb1, eb2 = st.columns([1, 1])
                all_err_indices = list(range(len(errors)))
                if eb1.button(f"✅ Allow All Errors ({len(errors)})", key="bulk_allow_err_rd"):
                    for i in all_err_indices:
                        st.session_state["err_decisions"][i] = "allow"
                    st.rerun()
                if eb2.button(f"🚫 Ignore All Errors ({len(errors)})", key="bulk_ignore_err_rd"):
                    for i in all_err_indices:
                        st.session_state["err_decisions"][i] = "ignore"
                    st.rerun()

                for i, err in enumerate(errors):
                    with st.container(border=True):
                        ec1, ec2 = st.columns([4, 1])
                        ec1.markdown(f"**Row {err.get('row')}** | `{err.get('reason')}`")
                        if err.get("email"):
                            ec1.markdown(f"**Email:** {err.get('email')}")
                        decision = ec2.radio(
                            "Action",
                            ["Allow", "Ignore"],
                            index=0 if st.session_state["err_decisions"].get(i) == "allow" else 1,
                            key=f"err_rd_{i}",
                            horizontal=False,
                        )
                        st.session_state["err_decisions"][i] = decision.lower()

            st.divider()
            if st.button("🚀 Push to Database (Live Run)", type="primary", key="commit_btn_rd", width="stretch"):
                csv_bytes = st.session_state.get("csv_file")
                csv_name = st.session_state.get("csv_filename", "students.csv")
                if not csv_bytes:
                    st.error("CSV file not found in session. Please re-upload and run dry run again.")
                else:
                    final_allowed_inc = [i for i, d in st.session_state.get("inc_decisions", {}).items() if d == "allow"]
                    _push_result = None
                    _push_error = None
                    with st.spinner("Committing changes..."):
                        files = {"file": (csv_name, csv_bytes, "text/csv")}
                        payload = [("allow_inconsistencies", str(idx)) for idx in final_allowed_inc]
                        payload.append(("ingest_new", "true"))
                        try:
                            r = st.session_state["http_session"].post(
                                f"{BASE_URL}/upload-students-csv/", files=files, data=payload, timeout=120
                            )
                            if r.status_code == 200:
                                _push_result = r.json()
                            else:
                                _push_error = f"Commit failed (HTTP {r.status_code}): {r.text}"
                        except Exception as e:
                            _push_error = f"Commit failed: {e}"
                    # rerun OUTSIDE spinner to avoid StopException
                    if _push_result is not None:
                        from datetime import datetime as _dt
                        st.session_state["commit_result"] = {
                            "ran_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "filename": csv_name,
                            "response": _push_result,
                        }
                        st.session_state.pop("csv_dry_run_result", None)
                        st.session_state.pop("csv_file", None)
                        st.session_state.pop("inc_decisions", None)
                        st.session_state.pop("err_decisions", None)
                        _trigger_cache_bust()
                        refresh_csv_logs()
                        # Navigate to Home Dashboard so Previous Runs is immediately visible
                        # Use _nav_pending so it's applied before the radio widget renders
                        st.session_state["_nav_pending"] = "🏠 Home Dashboard"
                        st.rerun()
                    elif _push_error:
                        st.error(_push_error)

    # Subtab: Export Data
    with csv_tabs[1]:
        st.subheader("Export Student Data")
        ex_course = st.selectbox("Export Course", ["All", "CDS", "AIE", "CDE", "CDA"], key="ex_course")
        ex_status = st.selectbox("Export Status", ["All"] + PIPELINE_STATUSES, key="ex_status")
        if st.button("📥 Prepare Download", key="ex_go"):
            download_url = f"{BASE_URL}/export-students/?course={ex_course if ex_course!='All' else ''}&status={ex_status if ex_status!='All' else ''}"
            st.markdown(f"**[Click here to Download CSV]({download_url})**")

    # Subtab: DB Health & History
    with csv_tabs[2]:
        st.subheader("🕑 Upload History & Undo")
        if st.button("🔄 Refresh History", key="refresh_history"):
            res = _api_get("list-csv-logs/")
            if res: st.session_state["csv_logs"] = res.get("logs", [])
            
        logs = st.session_state.get("csv_logs", [])
        if logs:
            for log in logs:
                with st.container(border=True):
                    ic1, ic2 = st.columns([3, 1])
                    ic1.markdown(f"**Batch {log['id']}** | {log['timestamp']}")
                    ic1.caption(log["summary"])
                    if ic2.button("🔄 UNDO", key=f"undo_{log['id']}"):
                        with st.spinner("Undoing..."):
                            res = _api_post("undo-csv-upload/", {"log_id": log["id"]})
                            if res.get("success"):
                                st.success("Batch undone!")
                                st.rerun()
                            else: st.error(res.get("error"))
        else:
            st.info("No recent logs found.")

        st.divider()
        st.subheader("🛡️ Database Integrity")
        if st.button("🔄 Scan for Inconsistencies", key="scan_go_redesign"):
            res = _api_get("db-inconsistencies/")
            if res and res.get("inconsistencies"):
                st.warning(f"Found {len(res['inconsistencies'])} issues.")
                df_inc = pd.DataFrame(res["inconsistencies"])
                # Ensure student_email exists in df before display
                cols_to_show = ["type", "severity", "details"]
                if "student_email" in df_inc.columns:
                    cols_to_show.insert(1, "student_email")
                st.write(df_inc[cols_to_show].to_html(index=False), unsafe_allow_html=True)
            else:
                st.success("No inconsistencies found!")

# ============================================================================
# TAB 5: TEAMS & PROJECTS
# ============================================================================
if tab_teams_projects:
    st.subheader("Team Formation & Project Management")
    tp_tabs = st.tabs(["🔍 Team Lookup", "🤝 Team Formation", "📋 Project Registry", "🎯 Bulk Assignment"])

    with tp_tabs[0]:
        st.write("### 🔍 Team Lookup")
        tl_col1, tl_col2 = st.columns([4, 1])
        tl_query = tl_col1.text_input("Team ID", placeholder="e.g. PTID-CDS-MAR-26-11122", label_visibility="collapsed", key="team_lookup_query")
        tl_search = tl_col2.button("Search", key="team_lookup_btn", width="stretch")

        if tl_search and tl_query.strip():
            res = _api_get("team-roster/", params={"team_id": tl_query.strip()})
            st.session_state["team_lookup_result"] = res

        result = st.session_state.get("team_lookup_result")
        if result:
            if not result.get("success"):
                st.error(result.get("error", "Team not found"))
            else:
                members = result.get("members", [])
                team_id = result.get("team_id", "")
                if not members:
                    st.warning(f"No members found for `{team_id}`.")
                else:
                    st.success(f"**{team_id}** — {len(members)} member(s)")
                    for m in members:
                        with st.container(border=True):
                            mc1, mc2 = st.columns([3, 1])
                            mc1.markdown(f"**{m['name']}**  \n{m['email']}")
                            mc2.caption(f"Preference: {m.get('team_preference', '—')}")
                            for enr in m.get("enrollments", []):
                                enr_team = enr.get("team_id") or "—"
                                st.caption(f"Course: **{enr['course']}** | Status: {enr['status']} | Team: {enr_team}")

    with tp_tabs[1]:
        st.write("### 🤖 Automated Team Formation")
        st.markdown("Group registered students into teams by course. Choose target size and handle remainders.")
        
        c_top1, c_top2, c_top3 = st.columns([2, 1, 1])
        t_size = c_top1.selectbox("Target Team Size", [2, 3, 4], index=2, key="target_t_size")
        if c_top2.button("🔄 Refresh Preview", key="tf_refresh", width="stretch"):
            res = _api_get("team-formation-preview/", params={"size": t_size})
            if res: st.session_state["tf_preview"] = res.get("courses", [])
        if c_top3.button("🧹 Purge Stale Prefs", key="tf_purge_all", width="stretch", help="Remove orphaned preference rows for students already assigned to teams or no longer Registered"):
            with st.spinner("Purging stale preference rows..."):
                pr = _api_post("purge-stale-preferences/", {})
            if pr.get("success"):
                deleted = pr.get("deleted", 0)
                breakdown = pr.get("breakdown", {})
                if deleted:
                    breakdown_str = ", ".join(f"{c}: {n}" for c, n in breakdown.items())
                    st.success(f"Purged {deleted} stale preference row(s). Breakdown: {breakdown_str}. Refresh Preview to update.")
                else:
                    st.info("No stale preference rows found.")
                _trigger_cache_bust()
            else:
                st.error(pr.get("error", "Purge failed"))

        preview_courses = st.session_state.get("tf_preview", [])
        if preview_courses:
            st.write("#### Course Breakdown")
            # Header
            hcols = st.columns([2, 1, 1, 1, 1, 2])
            hcols[0].write("**Course**")
            hcols[1].write("**Pending**")
            hcols[2].write("**Indiv**")
            hcols[3].write("**Teams**")
            hcols[4].write("**Rem**")
            hcols[5].write("**Action**")
            
            tf_settings = {}
            for c_idx, pc in enumerate(preview_courses):
                ccols = st.columns([2, 1, 1, 1, 1, 2])
                ccols[0].write(f"`{pc['course']}`")
                ccols[1].write(str(pc['total_students']))
                ccols[2].write(str(pc['individuals']))
                ccols[3].write(str(pc['full_teams']))
                ccols[4].write(str(pc['remainder']))
                
                with ccols[5]:
                    if pc['remainder'] > 0:
                        force = st.checkbox(f"Force team of {pc['remainder']}?", key=f"force_{c_idx}")
                        tf_settings[pc['course']] = {"force_remainder": force}
                    else:
                        st.caption("None")
                        tf_settings[pc['course']] = {"force_remainder": False}
            
            st.divider()
            if st.button("🚀 Form Teams Now", type="primary", width="stretch"):
                with st.spinner("Creating teams..."):
                    res = _api_post("team-formation-execute/", {"course_settings": tf_settings, "team_size": t_size})
                if res.get("error"):
                    st.error(res.get("error"))
                elif res.get("success"):
                    created = res.get("total_teams_created", 0)
                    assigned = res.get("total_students_assigned", 0)
                    if created == 0:
                        diags = res.get("diagnostics", [])
                        if diags:
                            for d in diags:
                                skip = d.get("skip_reason", "")
                                if skip == "no_registered_students":
                                    breakdown = d.get("status_breakdown", [])
                                    no_enroll = d.get("no_enrollment_count", 0)
                                    breakdown_str = ", ".join(
                                        f"{r['status']}: {r['count']}" for r in breakdown
                                    ) if breakdown else "none enrolled in this course"
                                    st.warning(
                                        f"**{d['course']}**: Found {d['preference_rows']} preference row(s) in Team table "
                                        f"but 0 students with status=Registered and no team assigned.  \n"
                                        f"Student status breakdown for those emails: **{breakdown_str}**"
                                        + (f"  \n{no_enroll} preference row(s) have no Student enrollment for this course (PATH B / orphan)." if no_enroll else "")
                                        + ("  \nIf most are **TeamIDGiven**, these are stale preference rows — click **Purge Stale** below." if any(r['status'] == 'TeamIDGiven' for r in breakdown) else "")
                                    )
                                    if st.button(f"🧹 Purge Stale Preference Rows ({d['course']})", key=f"purge_{d['course']}"):
                                        pr = _api_post("purge-stale-preferences/", {"course": d['course']})
                                        if pr.get("success"):
                                            st.success(f"Purged {pr['deleted']} stale row(s). Refresh Preview to update.")
                                            _trigger_cache_bust()
                                        else:
                                            st.error(pr.get("error", "Purge failed"))
                                elif skip.startswith("remainder_"):
                                    parts = skip.split("_")
                                    rem = parts[1]
                                    st.warning(
                                        f"**{d['course']}**: {d['registered_students']} registered student(s) found — "
                                        f"{d.get('individuals', 0)} individual(s), {d.get('group_seekers', 0)} group-seeker(s). "
                                        f"{rem} student(s) left in remainder (need {d['team_size']} to fill a team). "
                                        f"Check **'Force team of {rem}?'** in the preview table to assign anyway."
                                    )
                                else:
                                    st.warning(f"**{d['course']}**: 0 teams formed. {skip or 'Unknown reason.'}")
                        else:
                            st.warning("No teams were formed. Make sure students have submitted a preference and have status=Registered with no team assigned.")
                        with st.expander("Full API response"):
                            st.json(res)
                    else:
                        st.success(f"Formed {created} teams — {assigned} students assigned!")
                        _trigger_cache_bust()
                        st.session_state.pop("tf_preview", None)
                        st.rerun()
                else:
                    st.error("Unexpected response from server")
                    st.json(res)
        else:
            st.info("Click 'Refresh Preview' to see pending students.")

    with tp_tabs[2]:
        st.write("### Project Registry")
        pr_course = st.selectbox("Filter by Course", ["All", "CDS", "AIE", "CDE", "CDA"], key="pr_course_filter")
        pr_type = st.selectbox("Filter by Type", ["All", "Capstone", "Client"], key="pr_type_filter")
        p_res = _api_get("projects/", params={
            "course": pr_course if pr_course != "All" else "",
            "type": pr_type if pr_type != "All" else "",
        })

        if p_res and p_res.get("error"):
            st.error(f"Projects API error: {p_res['error']}")
        else:
            projects = (p_res or {}).get("results", [])
            if projects:
                df_p = pd.DataFrame(projects)
                disp_cols = [c for c in ["project_id", "project_name", "course", "project_type"] if c in df_p.columns]
                st.dataframe(df_p[disp_cols], width="stretch", hide_index=True)
            else:
                st.info("No projects found for selected filters.")

        with st.expander("➕ Add New Project"):
            ap_id = st.text_input("Project ID", key="ap_id")
            ap_name = st.text_input("Name", key="ap_name")
            ap_course = st.selectbox("Course", ["CDS", "AIE", "CDE", "CDA"], key="ap_course")
            ap_type = st.selectbox("Type", ["Capstone", "Client"], key="ap_type")
            if st.button("Create Project", key="ap_create"):
                if not ap_id.strip() or not ap_name.strip():
                    st.warning("Project ID and Name are required.")
                else:
                    r = _api_post("projects/", {"project_id": ap_id.strip(), "project_name": ap_name.strip(), "course": ap_course, "project_type": ap_type})
                    if r.get("success"): st.success("Project created!"); st.rerun()
                    else: st.error(r.get("error", "Failed"))

    with tp_tabs[3]:
        st.write("### Bulk Project Assignment")
        ba_course = st.selectbox("Target Course", ["CDS", "AIE", "CDE", "CDA"], key="ba_course")
        ba_pid = st.text_input("Project ID to Assign (e.g. PRAICP-CDS-001)")
        if st.button("Assign Project to All Teams", type="primary", key="ba_assign"):
            res = _api_post("bulk-assign-project/", {"project_id": ba_pid, "course": ba_course})
            if res.get("success"): st.success(f"Assigned to {res['assigned_count']} teams!")
            else: st.error(res.get("error"))

# ============================================================================
# TAB 6: EVALUATIONS
# ============================================================================
if tab_evaluations:
    st.subheader("📝 Evaluation Queue & Management")

    e_col1, e_col2, e_col3, e_col4 = st.columns(4)
    e_course = e_col1.selectbox("Course", ["All", "CDS", "AIE", "CDE", "CDA"], key="eval_course")
    e_rev = e_col2.selectbox("Reviewed", ["All", "Yes", "No"], key="eval_reviewed")
    e_sent = e_col3.selectbox("Email Sent", ["All", "Yes", "No"], key="eval_sent")
    e_grade = e_col4.selectbox("Grade", ["All", "A", "B", "C", "D"], key="eval_grade")

    e_params = {k: v for k, v in {
        "course": e_course if e_course != "All" else "",
        "reviewed": "true" if e_rev == "Yes" else ("false" if e_rev == "No" else ""),
        "email_sent": "true" if e_sent == "Yes" else ("false" if e_sent == "No" else ""),
        "grade": e_grade if e_grade != "All" else "",
    }.items() if v}

    e_res = _api_get("evaluations/", params=e_params)
    evs = (e_res or {}).get("results", [])

    if e_res and e_res.get("error"):
        st.error(f"Evaluations API error: {e_res.get('error')}")
    elif evs:
        # Bulk action buttons at top
        c_act1, c_act2 = st.columns(2)
        if c_act1.button("✅ Mark All Filtered as Reviewed", key="mark_all_rev"):
            ids = [x["evaluation_id"] for x in evs if "evaluation_id" in x]
            res = _api_post("bulk-mark-reviewed/", {"evaluation_ids": ids})
            if res.get("success"):
                st.success(f"Marked {res.get('updated_count', len(ids))} as reviewed.")
                _trigger_cache_bust()
                st.rerun()
            else:
                st.error(res.get("error", "Failed"))

        if c_act2.button("✉ Trigger Pending Emails", type="primary", key="fire_emails"):
            res = _api_post("trigger-pending-emails/", {})
            if res.get("success"):
                sent = res.get("emails_sent", 0)
                failed = res.get("failed", 0)
                if sent > 0:
                    st.success(f"✅ Sent {sent} email(s).")
                if failed > 0:
                    st.warning(f"⚠️ {failed} email(s) failed.")
                    with st.expander("Failed evaluations"):
                        for f in res.get("failures", []):
                            st.text(f"ID={f['id']}  {f['team']}  {f['project']}\n  → {f['reason']}")
                if sent == 0 and failed == 0:
                    st.info("Nothing pending to send right now.")
                _trigger_cache_bust()
            else:
                st.error(res.get("error", "Failed"))

        st.divider()

        # Summary table (no href links)
        df_ev = pd.DataFrame(evs)
        disp_cols = [c for c in ["team_id", "project_id", "course", "grade", "reviewed", "email_sent", "visibility_after"] if c in df_ev.columns]
        st.dataframe(df_ev[disp_cols], width="stretch", hide_index=True)

        st.divider()
        st.write("#### Row Actions")
        st.caption("Select an evaluation from the table above by entering its ID:")

        row_ev_id = st.number_input("Evaluation ID", min_value=1, step=1, key="row_ev_id")
        row_ev = next((x for x in evs if x.get("evaluation_id") == int(row_ev_id)), None) if row_ev_id else None

        if row_ev:
            st.write(f"**{row_ev['team_id']}** — {row_ev['project_id']} ({row_ev['course']}) | Grade: {row_ev.get('grade') or '—'} | Reviewed: {row_ev['reviewed']} | Sent: {row_ev['email_sent']}")
            ra1, ra2, ra3 = st.columns(3)

            if ra1.button("✅ Mark Reviewed", key="row_mark_rev"):
                res = st.session_state["http_session"].request(
                    "PATCH", f"{BASE_URL}/evaluations/{row_ev_id}/",
                    json={"reviewed": True}, timeout=15
                )
                if res.status_code == 200 and res.json().get("success"):
                    st.success("Marked as reviewed.")
                    _trigger_cache_bust()
                    st.rerun()
                else:
                    st.error("Failed to mark reviewed.")

            if ra2.button("✉ Resend Email", key="row_resend"):
                res = _api_post("resend-evaluation-email/", {"evaluation_id": int(row_ev_id)})
                if res.get("success"):
                    st.success("Email resent!")
                    _trigger_cache_bust()
                else:
                    st.error(res.get("error", "Failed"))

            if ra3.button("🗑 Delete Evaluation", type="secondary", key="row_delete"):
                if st.session_state.get("confirm_eval_delete") == row_ev_id:
                    res = st.session_state["http_session"].request(
                        "DELETE", f"{BASE_URL}/evaluations/{row_ev_id}/delete/", timeout=15
                    )
                    if res.status_code == 200 and res.json().get("success"):
                        st.success("Evaluation deleted and TeamProject reset to assigned.")
                        st.session_state.pop("confirm_eval_delete", None)
                        _trigger_cache_bust()
                        st.rerun()
                    else:
                        st.error("Delete failed.")
                else:
                    st.session_state["confirm_eval_delete"] = row_ev_id
                    st.warning("Click Delete again to confirm. This resets the TeamProject to 'assigned'.")
        else:
            st.info("Enter an evaluation ID above to take row-level actions.")
    else:
        st.info("No evaluations found matching filters.")

# ============================================================================
# TAB 7: CRON JOBS
# ============================================================================
if tab_cron:
    st.subheader("🕐 Scheduled Jobs")

    # Inline trigger result stored in session so it survives a rerun
    if "cron_trigger_result" not in st.session_state:
        st.session_state["cron_trigger_result"] = None

    cron_res = _api_get("cron-status/")
    jobs = (cron_res or {}).get("jobs", [])

    if not jobs:
        st.warning("No cron data available. Make sure `RUN_SCHEDULER=True` and the server has been running long enough for at least one job to register.")
    else:
        STATUS_COLOR = {
            "success": "🟢",
            "partial": "🟡",
            "Executed": "🟢",
            "error": "🔴",
            "Error": "🔴",
            "Miss": "🟡",
        }

        from datetime import datetime as _dt

        for job in jobs:
            job_id = job.get("job_id", "")
            status_raw = job.get("status") or "unknown"
            icon = STATUS_COLOR.get(status_raw, "⚪")

            last_run = job.get("last_run") or "Never"
            try:
                dt = _dt.fromisoformat(last_run.replace("Z", "+00:00"))
                last_run = dt.strftime("%d %b %Y  %H:%M UTC")
            except Exception:
                pass

            with st.container(border=True):
                # Header row: label + schedule + status + Run Now button
                h1, h2, h3 = st.columns([3, 1, 1])
                h1.markdown(f"**{job['label']}**  \n`{job['schedule']}`")
                h2.markdown(f"{icon} **{status_raw}**")

                # Run Now button only for the eval emails job
                if job_id == "send_pending_evaluation_emails":
                    if h3.button("▶ Run Now", key=f"run_now_{job_id}", type="primary"):
                        with st.spinner("Running job…"):
                            trig = _api_post("trigger-pending-emails/", {})
                        if trig:
                            st.session_state["cron_trigger_result"] = trig
                            st.rerun()

                # Timing row
                col_a, col_b = st.columns(2)
                col_a.caption(f"Last run: {last_run}")
                if job.get("duration"):
                    col_b.caption(f"Duration: {job['duration']}")

                # Exception banner
                if job.get("exception"):
                    st.error(f"Exception: {job['exception']}")

                # Retained summary from last run (auto or manual)
                if job.get("summary"):
                    with st.expander("📋 Last run summary"):
                        st.text(job["summary"])

        # Show inline result from the most recent manual trigger
        trig_res = st.session_state.get("cron_trigger_result")
        if trig_res:
            st.divider()
            sent = trig_res.get("emails_sent", 0)
            failed = trig_res.get("failed", 0)
            err = trig_res.get("error")
            if err:
                st.error(f"Run failed: {err}")
            elif failed == 0:
                st.success(f"✅ Run complete — Sent: **{sent}**  Failed: **{failed}**")
            else:
                st.warning(f"⚠️ Run complete — Sent: **{sent}**  Failed: **{failed}**")
                failures = trig_res.get("failures", [])
                if failures:
                    with st.expander(f"Show {len(failures)} failure(s)"):
                        for f in failures:
                            st.text(f"ID={f['id']}  {f['team']}  {f['project']}: {f['reason']}")
            if st.button("Dismiss", key="dismiss_cron_result"):
                st.session_state["cron_trigger_result"] = None
                st.rerun()

    st.divider()
    st.caption("Jobs run automatically via APScheduler (`RUN_SCHEDULER=True`). "
               "Execution history is stored in `django_apscheduler_djangojobexecution`. "
               "Summaries are written to the Django cache after each run and persist across restarts.")


# TAB 8: CONTROL CENTER (Enhanced)
# ============================================================================
if tab_cc:
    st.header("⚙️ Control Center")

    cc_tabs = st.tabs(["👤 Manage Admins", "📜 Audit Logs", "📖 Pipeline Glossary"])

    with cc_tabs[0]:
        st.subheader("Manage Admins")
        adm_res = _api_get_cc("admins/")
        admins = (adm_res or {}).get("admins", [])

        if adm_res and adm_res.get("error"):
            st.error(adm_res["error"])
        elif admins:
            df_adm = pd.DataFrame(admins)
            disp = [c for c in ["email", "name", "role", "is_active", "last_login"] if c in df_adm.columns]
            st.dataframe(df_adm[disp], width="stretch", hide_index=True)
        else:
            st.info("No admins found.")

        st.divider()
        with st.expander("➕ Create / Update Admin"):
            na_email = st.text_input("Email", key="na_email")
            na_name = st.text_input("Name", key="na_name")
            na_role = st.selectbox("Role", ["Admin", "SuperAdmin"], key="na_role")
            na_pass = st.text_input("Password", type="password", key="na_pass")
            na_active = st.checkbox("Active", value=True, key="na_active")
            if st.button("Save Admin", key="na_save"):
                payload = {"email": na_email, "name": na_name, "role": na_role, "is_active": na_active}
                if na_pass: payload["password"] = na_pass
                res = _api_post_cc("admins/", payload)
                if res.get("success"): st.success(res["message"]); st.rerun()
                else: st.error(res.get("error", "Failed"))

    with cc_tabs[1]:
        st.subheader("Audit Logs")
        log_res = _api_get_cc("audit-logs/")
        logs = (log_res or {}).get("logs", [])

        if log_res and log_res.get("error"):
            st.error(log_res["error"])
        elif logs:
            df_log = pd.DataFrame(logs)
            disp = [c for c in ["timestamp", "admin_email", "action", "target_model", "target_id", "ip_address"] if c in df_log.columns]
            if "timestamp" in df_log.columns:
                df_log["timestamp"] = pd.to_datetime(df_log["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
            st.dataframe(df_log[disp], width="stretch", hide_index=True)
        else:
            st.info("No audit logs found.")

    with cc_tabs[2]:
        st.info("The Glossary has been moved to the **Home Dashboard** for access by all administrators.")

