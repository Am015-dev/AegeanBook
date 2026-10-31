import streamlit as st

from ui import CSS

st.set_page_config(page_title="AEGEAN fare tracker", page_icon="✈️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
st.logo("static/logo.svg", size="large")
# the Overview is the one place to find a trip (route or Anywhere, with the weekday and booking-ahead insights under its answer);
# the Expert explains it; everything else is a tool under More
st.navigation({"": [st.Page("views/overview.py", title="Overview", icon=":material/space_dashboard:", default=True),
                    st.Page("views/expert.py", title="Expert", icon=":material/auto_awesome:", url_path="expert")],
               "More": [st.Page("views/flights.py", title="Flights", icon=":material/flight_takeoff:", url_path="flights"),
                        st.Page("views/hotel.py", title="Hotels", icon=":material/hotel:"),
                        st.Page("views/alerts.py", title="Alerts", icon=":material/notifications:", url_path="alerts"),
                        st.Page("views/database.py", title="Database", icon=":material/database:"),
                        st.Page("views/stats.py", title="Statistics", icon=":material/bar_chart:"),
                        st.Page("views/health.py", title="Health", icon=":material/monitor_heart:")]}, position="top").run()
