import streamlit as st

from ui import CSS

st.set_page_config(page_title="AEGEAN fare tracker", page_icon="✈️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
st.logo("static/logo.svg", size="large")
# the menu by what a traveller came to do: find a flight, find a hotel, be told when a fare drops, ask why; the day-by-day fare tool
# and the data behind the app sit together under Data
st.navigation({"": [st.Page("views/overview.py", title="Flights", icon=":material/flight_takeoff:", default=True),
                    st.Page("views/hotel.py", title="Hotels", icon=":material/hotel:"),
                    st.Page("views/alerts.py", title="Alerts", icon=":material/notifications:", url_path="alerts"),
                    st.Page("views/expert.py", title="Ask the expert", icon=":material/auto_awesome:", url_path="expert")],
               "Data": [st.Page("views/flights.py", title="Fare calendar and miles", icon=":material/calendar_month:", url_path="flights"),
                        st.Page("views/database.py", title="Database", icon=":material/database:"),
                        st.Page("views/stats.py", title="Statistics", icon=":material/bar_chart:"),
                        st.Page("views/health.py", title="Status", icon=":material/monitor_heart:")]}, position="top").run()
