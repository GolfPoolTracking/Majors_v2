"""Outgoing emails to entrants (confirmations, edit links, field changes)."""
import html
import smtplib
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import streamlit as st

from auth import make_magic_link_hash

BASE_URL = "https://golfmajors-v2.streamlit.app/"


def _send_email_async(msg, sender, pwd):
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, pwd)
        server.send_message(msg) 
        server.quit()
    except Exception as e:
        print(f"Async Email Error: {e}")

def send_field_change_email(user_email, name, removed_picks, added_to_field, t_name, t_id, close_time_str, logo_url):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    if not sender or not pwd: return False
    try:
        msg = MIMEMultipart()
        msg['From'] = formataddr((t_name, sender))
        msg['To'] = user_email
        
        logo_html = f'<div style="text-align: center; margin-bottom: 20px;"><img src="{logo_url}" style="max-height: 85px; max-width: 100%;" alt="{t_name} Logo"></div>' if logo_url else f'<div style="text-align: center; border-bottom: 2px solid #2ecc71; padding-bottom: 10px; margin-bottom: 20px;"><h2 style="color: #27ae60; margin: 0;">{t_name} Sweepstakes</h2></div>'
        public_link = f"{BASE_URL}?view=public&tourney_id={t_id}"
        
        blocks = []
        subject = f"{t_name} - ⚠️ Tournament Field Update"
        
        if removed_picks:
            subject = f"{t_name} - 🚨 URGENT: Action Required for your Team"
            p_html = "".join([f"<li style='padding: 4px 0;'><b>{html.escape(p)}</b></li>" for p in removed_picks])
            blocks.append(f"""
            <div style="background-color: #fff3e0; border-left: 4px solid #e67e22; padding: 15px 20px; border-radius: 4px; margin: 20px 0;">
                <h3 style="margin-top:0; color: #d35400; font-size: 18px;">🚨 Player(s) Withdrawn</h3>
                <p style="font-size: 15px;">One or more players you selected are no longer in the tournament field:</p>
                <ul style="font-size: 15px; color: #c0392b;">{p_html}</ul>
                <p style="font-size: 15px; font-weight: bold;">Please go to the Tournament Dashboard to update your entry before the deadline: <br><span style="color:#d35400;">{close_time_str}</span>.</p>
            </div>
            """)
            
        if added_to_field:
            p_html = "".join([f"<li>{html.escape(p)}</li>" for p in added_to_field])
            blocks.append(f"""
            <div style="background-color: #e8f8f5; border-left: 4px solid #1abc9c; padding: 15px 20px; border-radius: 4px; margin: 20px 0;">
                <h3 style="margin-top:0; color: #16a085; font-size: 18px;">⛳ New Players Added</h3>
                <p style="font-size: 15px;">The following players have just been added to the tournament field. You may edit your team to include them if you wish:</p>
                <ul style="font-size: 15px; color: #2c3e50;">{p_html}</ul>
            </div>
            """)
            
        body = f"""
        <html>
          <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #2c3e50; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px;">
            {logo_html}
            <p style="font-size: 16px;">Hi <b>{html.escape(name)}</b>,</p>
            {"".join(blocks)}
            
            <div style="text-align: center; margin-top: 35px; margin-bottom: 20px;">
                <a href="{public_link}" target="_blank" style="background-color: #34495e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; display: inline-block; font-weight: bold; font-size: 15px;">Go to Tournament Dashboard</a>
            </div>
          </body>
        </html>
        """
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        
        threading.Thread(target=_send_email_async, args=(msg, sender, pwd)).start()
        return True
    except Exception as e: 
        print(f"Email Error (send_field_change): {e}")
        return False

def send_confirmation_email(user_email, name, picks, tie_breaker, payment, t_name, t_id, is_edit=False, close_time_str="", logo_url=""):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    if not sender or not pwd: return False
    try:
        msg = MIMEMultipart()
        msg['From'] = formataddr((t_name, sender))
        msg['To'] = user_email
        status_word = "UPDATED" if is_edit else "LOCKED IN"
        msg['Subject'] = f"{t_name} - ⛳ Your Team is {status_word}"
        
        p_html = "".join([f"<li style='padding: 4px 0;'><b>{html.escape(p)}</b></li>" for p in picks])
        public_link = f"{BASE_URL}?view=public&tourney_id={t_id}"
        
        edit_notice = f"<p style='color: #e67e22; font-size: 14px; background-color: #fff3e0; padding: 10px; border-radius: 4px; border-left: 3px solid #e67e22;'>✏️ <b>Need to make a change?</b> You can edit your team anytime before <b>{close_time_str}</b>. Just go to the <a href='{public_link}' target='_blank'>Tournament Dashboard</a>, click the <b>🔍 Check Entry</b> tab, and enter your email address to securely unlock your entry!</p>" if close_time_str else ""
        
        logo_html = f'<div style="text-align: center; margin-bottom: 20px;"><img src="{logo_url}" style="max-height: 85px; max-width: 100%;" alt="{t_name} Logo"></div>' if logo_url else f'<div style="text-align: center; border-bottom: 2px solid #2ecc71; padding-bottom: 10px; margin-bottom: 20px;"><h2 style="color: #27ae60; margin: 0;">{t_name} Sweepstakes</h2></div>'
        
        body = f"""
        <html>
          <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #2c3e50; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px;">
            {logo_html}
            <p style="font-size: 16px;">Hi <b>{html.escape(name)}</b>,</p>
            <p style="font-size: 16px;">Your entry has been securely <b>{status_word.lower()}</b>! Here are your official team picks for the tournament:</p>
            
            <div style="background-color: #f8f9fa; border-left: 4px solid #2ecc71; padding: 15px 20px; border-radius: 4px; margin: 20px 0;">
                <h3 style="margin-top:0; color: #2c3e50; font-size: 18px;">🏌️ Your Starting 5</h3>
                <ul style="font-size: 16px; list-style-type: none; padding-left: 0;">
                  {p_html}
                </ul>
                <hr style="border: 0; border-top: 1px solid #dee2e6; margin: 15px 0;">
                <p style="margin: 5px 0; font-size: 15px;"><b>Tie Breaker Score:</b> <span style="color: #2980b9; font-weight: bold;">{tie_breaker}</span></p>
                <p style="margin: 5px 0; font-size: 15px;"><b>Payment Method:</b> {html.escape(payment)}</p>
            </div>
            
            {edit_notice}
            
            <div style="text-align: center; margin: 35px 0 20px 0;">
                <a href="{public_link}" target="_blank" style="background-color: #34495e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; display: inline-block; font-weight: bold; font-size: 15px;">View Live Leaderboard</a>
            </div>
          </body>
        </html>
        """
        msg.attach(MIMEText(body, 'html'))
        
        msg['Bcc'] = sender 
        
        threading.Thread(target=_send_email_async, args=(msg, sender, pwd)).start()
        return True
    except Exception as e: 
        print(f"Email Error (send_confirmation): {e}")
        return False

def send_magic_link_email(user_email, t_name, t_id, magic_secret, logo_url=""):
    sender, pwd = st.secrets.get("email_sender"), st.secrets.get("email_password")
    if not sender or not pwd: return False
    try:
        ts = int(time.time()) 
        clean_email = str(user_email).strip().lower()
        auth_hash = make_magic_link_hash(clean_email, t_id, ts, magic_secret)
        import urllib.parse
        safe_email = urllib.parse.quote(clean_email)
        link = f"{BASE_URL}?view=public&tourney_id={t_id}&edit_email={safe_email}&auth={auth_hash}&ts={ts}"
        
        msg = MIMEMultipart()
        msg['From'] = formataddr((t_name, sender))
        msg['To'] = user_email
        msg['Subject'] = f"{t_name} - 🔐 Secure Edit Link"
        
        logo_html = f'<div style="text-align: center; margin-bottom: 20px;"><img src="{logo_url}" style="max-height: 85px; max-width: 100%;" alt="{t_name} Logo"></div>' if logo_url else ""
        
        body = f"""
        <html>
          <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #2c3e50; line-height: 1.6; max-width: 600px; margin: 0 auto; padding: 20px;">
            {logo_html}
            <p style="font-size: 16px;">Hi,</p>
            <p style="font-size: 16px;">You requested a secure link to edit your team for the <b>{t_name} Sweepstakes</b>.</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{link}" target="_blank" style="background-color: #34495e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; display: inline-block; font-weight: bold; font-size: 15px;">✏️ Edit Your Team</a>
            </div>
            <p style="font-size: 14px; color: #7f8c8d;">This link is uniquely tied to your email address and grants direct access to your entry form. For security, this link will automatically expire in 1 hour.</p>
          </body>
        </html>
        """
        msg.attach(MIMEText(body, 'html'))
        
        threading.Thread(target=_send_email_async, args=(msg, sender, pwd)).start()
        return True
    except Exception as e: 
        print(f"Email Error (send_magic_link): {e}")
        return False
