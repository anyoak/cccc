import time
import re
import requests
import os
import random
from datetime import datetime, timedelta
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains
import phonenumbers
from phonenumbers import region_code_for_number
import pycountry
import config
import threading
from concurrent.futures import ThreadPoolExecutor

# Global variables
last_messages = set()
monitoring_started = False
bot_start_time = None
monitoring_active = True
executor = ThreadPoolExecutor(max_workers=5)
refresh_pattern_index = 0
refresh_warning_msg_id = None  # Track refresh warning message

# Refresh pattern in seconds
REFRESH_PATTERN = [1600, 1405, 1100, 1250, 1350]  # 5 minutes average

os.makedirs(config.DOWNLOAD_FOLDER, exist_ok=True)

def human_like_delay(min_seconds=1, max_seconds=3):
    """Human-like random delay"""
    time.sleep(random.uniform(min_seconds, max_seconds))

def human_like_mouse_movement(driver, element):
    """Simulate human-like mouse movement"""
    try:
        location = element.location
        size = element.size
        
        offset_x = random.randint(0, size['width'] // 2)
        offset_y = random.randint(0, size['height'] // 2)
        
        action = ActionChains(driver)
        action.move_to_element_with_offset(element, offset_x, offset_y)
        action.pause(random.uniform(0.1, 0.3))
        action.click()
        action.perform()
    except:
        element.click()

def get_next_refresh_time():
    """Get next refresh time using the specified pattern"""
    global refresh_pattern_index
    
    interval = REFRESH_PATTERN[refresh_pattern_index]
    refresh_pattern_index = (refresh_pattern_index + 1) % len(REFRESH_PATTERN)
    
    print(f"[🔄] Next refresh in {interval} seconds ({interval//60} minutes {interval%60} seconds)")
    return interval

def mask_number(number):
    """Mask phone number for privacy"""
    digits = re.sub(r"\D", "", number)
    if len(digits) > 6:
        return digits[:4] + "***" + digits[-3:]
    return number

def country_to_flag(country_code):
    """Convert country code to flag emoji"""
    if not country_code or len(country_code) != 2:
        return "🏳️"
    return "".join(chr(127397 + ord(c)) for c in country_code.upper())

def detect_country(number):
    """Detect country from phone number"""
    try:
        clean_number = re.sub(r"\D", "", number)
        if clean_number:
            parsed = phonenumbers.parse("+" + clean_number, None)
            region = region_code_for_number(parsed)
            country = pycountry.countries.get(alpha_2=region)
            if country:
                return country.name, country_to_flag(region)
    except:
        pass
    return "Unknown", "🏳️"

def extract_otp(message):
    """Extract OTP code from message"""
    patterns = [
        r'\b\d{3}-\d{3}\b',  # 123-456
        r'\b\d{3} \d{3}\b',  # 123 456
        r'\b\d{6}\b',        # 123456
        r'\b\d{4}\b',        # 1234
        r'\b\d{5}\b',        # 12345
        r'\b\d{7}\b',        # 1234567
        r'\b\d{8}\b',        # 12345678
        r'code[:\s]*(\d+)',  # code: 123456
        r'Code[:\s]*(\d+)',  # Code: 123456
        r'CODE[:\s]*(\d+)',  # CODE: 123456
        r'verification[:\s]*(\d+)',  # verification: 123456
        r'password[:\s]*(\d+)',      # password: 123456
    ]
    
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            numbers = re.findall(r'\d+', match.group(0))
            if numbers:
                return numbers[0]
    return "N/A"

def extract_phone_number_from_text(text):
    """Extract phone number from text containing country and number"""
    patterns = [
        r'\+\d{10,15}',  # +123456789012
        r'\d{10,15}',    # 123456789012
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # US format
        r'\b\d{2}[-.]?\d{4}[-.]?\d{4}\b',  # International
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    
    return text.strip()

def send_message(text, delete_after=None, is_log=False):
    """Send message to Telegram with optional auto-delete"""
    try:
        url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
        
        # For logs, no buttons. For SMS, two buttons side by side
        if not is_log:
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "🔗 Number", "url": "https://t.me/incomes2025"},
                        {"text": "✨ Support", "url": "https://t.me/Manikul"}
                    ]
                ]
            }
            payload = {
                "chat_id": config.CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": keyboard,
            }
        else:
            payload = {
                "chat_id": config.CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            }
        
        res = requests.post(url, json=payload, timeout=10)
        if res.ok:
            msg_id = res.json().get("result", {}).get("message_id")
            if delete_after and msg_id:
                threading.Timer(delete_after, delete_message, args=[msg_id]).start()
            
            if is_log:
                print(f"[📋] Log sent to Telegram")
            else:
                print("[✅] SMS sent to Telegram")
            return msg_id
    except Exception as e:
        print(f"[❌] Failed to send message: {e}")
    return None

def delete_message(msg_id):
    """Delete message from Telegram"""
    try:
        url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/deleteMessage"
        requests.post(url, data={"chat_id": config.CHAT_ID, "message_id": msg_id}, timeout=5)
        print(f"[🗑️] Message {msg_id} deleted")
    except Exception as e:
        print(f"[❌] Failed to delete message {msg_id}: {e}")

def send_bot_started_message():
    """Send bot started message to group"""
    global bot_start_time
    bot_start_time = datetime.now()
    start_time_str = bot_start_time.strftime("%Y-%m-%d %I:%M:%S %p")
    
    message = (
        "🤖 **iVASMS SMS Monitor Started**\n\n"
        "✅ **Status:** `Monitoring Active`\n"
        f"⏰ **Start Time:** `{start_time_str}`\n"
        "🔄 **Auto Refresh:** `Every 5 minutes`\n"
        "📱 **Platform:** `iVASMS`"
    )
    
    msg_id = send_message(message, is_log=True)
    print("[🤖] Bot started message sent to group")
    return msg_id

def send_refresh_warning():
    """Send browser refresh warning 10 seconds before refresh - WILL DELETE AFTER 10 SECONDS"""
    global refresh_warning_msg_id
    
    message = (
        "🔄 **Browser Refresh Alert**\n\n"
        "⚠️ Browser will refresh in *10 seconds*\n"
        "📊 Monitoring will pause briefly...\n"
        "⏳ Please wait for completion message"
    )
    
    refresh_warning_msg_id = send_message(message, is_log=True)
    print("[⚠️] Refresh warning sent to group (will delete in 10s)")
    
    # Delete this message after exactly 10 seconds
    if refresh_warning_msg_id:
        threading.Timer(10.0, delete_message, args=[refresh_warning_msg_id]).start()
    
    return refresh_warning_msg_id

def send_refresh_complete():
    """Send browser refresh completion message"""
    global bot_start_time
    
    current_time = datetime.now()
    uptime = current_time - bot_start_time if bot_start_time else timedelta(0)
    uptime_str = str(uptime).split('.')[0]
    
    message = (
        "✅ **Browser Refresh Complete**\n\n"
        "🔄 Refresh: SUCCESSFUL\n"
        f"⏰ Uptime: {uptime_str}\n"
        "📊 Monitoring: RESUMED\n"
        "🔥 Back to active monitoring..."
    )
    
    msg_id = send_message(message, delete_after=30, is_log=True)
    print("[✅] Refresh complete message sent to group (will delete in 30s)")
    return msg_id

def send_monitoring_stopped(reason="Unknown reason"):
    """Send monitoring stopped alert"""
    global bot_start_time
    
    current_time = datetime.now()
    if bot_start_time:
        uptime = current_time - bot_start_time
        uptime_str = str(uptime).split('.')[0]
    else:
        uptime_str = "Unknown"
    
    message = (
        "❌ **Monitoring Stopped**\n\n"
        f"📛 **Reason:** `{reason}`\n"
        f"⏰ **Uptime:** `{uptime_str}`\n"
        f"🕒 **Time:** `{current_time.strftime('%Y-%m-%d %I:%M:%S %p')}`\n\n"
        "⚠️ Attention required!\n"
        "🔄 Please restart the bot..."
    )
    
    msg_id = send_message(message, delete_after=900, is_log=True)
    print(f"[❌] Monitoring stopped alert sent to group (will delete in 15m): {reason}")
    return msg_id

def solve_cloudflare_captcha_advanced(driver):
    """Advanced CloudFlare CAPTCHA solver with multiple approaches"""
    try:
        print("[🛡️] Advanced CloudFlare CAPTCHA solver activated...")
        
        # Approach 1: Try to find and click the CAPTCHA iframe
        try:
            captcha_iframe = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 
                    "iframe[src*='challenges.cloudflare.com'], iframe[title*='challenge'], iframe[src*='recaptcha']"))
            )
            
            if captcha_iframe:
                print("[🔍] CAPTCHA iframe found, attempting auto-completion...")
                driver.switch_to.frame(captcha_iframe)
                
                checkbox_selectors = [
                    "input[type='checkbox']",
                    ".recaptcha-checkbox-border",
                    "#recaptcha-anchor",
                    ".cf-challenge-checkbox",
                    ".h-captcha",
                    ".checkbox"
                ]
                
                for selector in checkbox_selectors:
                    try:
                        checkbox = WebDriverWait(driver, 3).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                        if checkbox:
                            print(f"[✅] CAPTCHA checkbox found with selector: {selector}")
                            human_like_delay(2, 4)
                            human_like_mouse_movement(driver, checkbox)
                            print("[👆] CAPTCHA checkbox clicked, waiting for verification...")
                            human_like_delay(8, 12)
                            
                            try:
                                verified_indicator = driver.find_element(By.CSS_SELECTOR, ".recaptcha-checkbox-checked, .cf-challenge-success")
                                if verified_indicator:
                                    print("[🎉] CAPTCHA verification completed automatically!")
                                    driver.switch_to.default_content()
                                    return True
                            except:
                                pass
                            break
                    except:
                        continue
                
                driver.switch_to.default_content()
                
        except TimeoutException:
            print("[⏱️] No CAPTCHA iframe found with first approach")
            driver.switch_to.default_content()
        
        # Approach 2: Look for challenge form and submit
        try:
            challenge_form = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "form[action*='challenge'], .challenge-form"))
            )
            
            if challenge_form:
                print("[🔍] Challenge form found, looking for submit button...")
                
                submit_selectors = [
                    "input[type='submit']",
                    "button[type='submit']",
                    ".btn-success",
                    ".button--success",
                    "[type='submit']"
                ]
                
                for selector in submit_selectors:
                    try:
                        submit_btn = challenge_form.find_element(By.CSS_SELECTOR, selector)
                        if submit_btn and submit_btn.is_enabled():
                            human_like_delay(2, 3)
                            human_like_mouse_movement(driver, submit_btn)
                            print("[✅] Challenge form submitted")
                            human_like_delay(5, 8)
                            return True
                    except:
                        continue
                        
        except TimeoutException:
            print("[⏱️] No challenge form found")
        
        # Approach 3: Direct CAPTCHA element detection
        captcha_elements = driver.find_elements(By.CSS_SELECTOR, 
            ".cf-captcha, .captcha, .hcaptcha, .g-recaptcha, [data-sitekey]")
        
        if captcha_elements:
            print(f"[🔍] Found {len(captcha_elements)} CAPTCHA elements, attempting interaction...")
            
            for element in captcha_elements:
                try:
                    if element.is_displayed() and element.is_enabled():
                        human_like_delay(1, 2)
                        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
                        human_like_delay(1, 2)
                        human_like_mouse_movement(driver, element)
                        print("[👆] CAPTCHA element clicked")
                        human_like_delay(6, 10)
                        return True
                except:
                    continue
        
        print("[❌] CAPTCHA auto-completion failed with all approaches")
        return False
        
    except Exception as e:
        print(f"[💥] Advanced CAPTCHA solver error: {e}")
        driver.switch_to.default_content()
        return False

def check_and_solve_captcha(driver):
    """Check for CAPTCHA and attempt to solve it with enhanced detection"""
    try:
        captcha_indicators = [
            "challenges.cloudflare.com",
            "cf-challenge", 
            "recaptcha",
            "hcaptcha",
            "Just a moment",
            "Checking your browser",
            "Verifying you are human",
            "Challenge",
            "Security check"
        ]
        
        page_source = driver.page_source.lower()
        current_url = driver.current_url.lower()
        page_title = driver.title.lower()
        
        for indicator in captcha_indicators:
            indicator_lower = indicator.lower()
            if (indicator_lower in page_source or 
                indicator_lower in current_url or 
                indicator_lower in page_title):
                print(f"[🛡️] CAPTCHA detected: {indicator}")
                return solve_cloudflare_captcha_advanced(driver)
        
        captcha_selectors = [
            ".cf-captcha",
            ".captcha", 
            ".hcaptcha",
            ".g-recaptcha",
            "[data-sitekey]",
            "iframe[src*='captcha']",
            "iframe[src*='challenge']"
        ]
        
        for selector in captcha_selectors:
            try:
                if driver.find_elements(By.CSS_SELECTOR, selector):
                    print(f"[🔍] CAPTCHA element found with selector: {selector}")
                    return solve_cloudflare_captcha_advanced(driver)
            except:
                continue
                
        return False
        
    except Exception as e:
        print(f"[❌] Error checking CAPTCHA: {e}")
        return False

def safe_refresh_with_advanced_captcha(driver):
    """Safe page refresh with advanced CAPTCHA handling"""
    max_retries = 2
    retry_count = 0
    
    # Send refresh warning - it will auto-delete after 10 seconds
    send_refresh_warning()
    time.sleep(10)  # Wait exactly 10 seconds
    
    while retry_count <= max_retries:
        try:
            print(f"[🔄] Refreshing page... (Attempt {retry_count + 1})")
            driver.refresh()
            human_like_delay(3, 5)
            
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            human_like_delay(2, 4)
            
            if check_and_solve_captcha(driver):
                print("[✅] CAPTCHA auto-completed successfully")
                human_like_delay(5, 8)
                
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.ID, "LiveTestSMS"))
                    )
                    print("[✅] Successfully bypassed CAPTCHA and loaded main content")
                    send_refresh_complete()
                    return True
                except TimeoutException:
                    print("[⚠️] Main content not loaded after CAPTCHA, might need retry")
                    retry_count += 1
                    continue
            else:
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.ID, "LiveTestSMS"))
                    )
                    print("[✅] Page refreshed successfully without CAPTCHA")
                    send_refresh_complete()
                    return True
                except TimeoutException:
                    print("[⚠️] Main content not loaded, might have hidden CAPTCHA")
                    retry_count += 1
                    continue
                    
        except Exception as e:
            print(f"[❌] Refresh attempt {retry_count + 1} failed: {e}")
            retry_count += 1
            human_like_delay(5, 10)
    
    print("[💥] All refresh attempts failed")
    return False

def handle_captcha_protection(sb, url, step_name):
    """Handle CAPTCHA protection with advanced auto-solving"""
    print(f"🛡️ CAPTCHA protection check for {step_name}...")
    
    try:
        sb.driver.uc_open_with_reconnect(url, reconnect_time=3)
        human_like_delay(2, 4)
        
        if check_and_solve_captcha(sb.driver):
            print(f"✅ CAPTCHA auto-completion successful for {step_name}")
        else:
            print(f"[🔧] Using enhanced fallback CAPTCHA handling for {step_name}")
            try:
                sb.uc_gui_click_captcha()
            except:
                print("[⚠️] Manual CAPTCHA click failed, continuing...")
        
        human_like_delay(3, 5)
        return True
        
    except Exception as e:
        print(f"[❌] CAPTCHA handling error for {step_name}: {e}")
        return False

def wait_for_login_and_redirect(sb):
    """Wait for user to login manually and detect successful login"""
    global monitoring_started
    
    print("=" * 50)
    print("🤖 iVASMS SMS EXTRACTOR")
    print("=" * 50)
    print("[*] Please login manually to iVASMS...")
    print(f"[*] URL: {config.LOGIN_URL}")
    print("[*] Waiting for you to complete login...")
    print("=" * 50)
    
    # Navigate to login page with CAPTCHA protection
    handle_captcha_protection(sb, config.LOGIN_URL, "Login Page")
    
    # Wait for user to reach portal (successful login)
    try:
        WebDriverWait(sb.driver, 300).until(  # 5 minutes timeout
            EC.url_contains("/portal")
        )
        print("[✅] Login detected! Redirecting to SMS page...")
        
        # Send bot started message
        send_bot_started_message()
        
        # Navigate to SMS page with CAPTCHA protection
        handle_captcha_protection(sb, config.SMS_URL, "SMS Page")
        time.sleep(3)
        
        monitoring_started = True
        return True
        
    except Exception as e:
        print(f"[❌] Login timeout or failed: {e}")
        return False

def extract_ivasms_sms(driver):
    """Extract SMS messages from iVASMS live SMS page"""
    global last_messages
    
    try:
        # Check if we're on the SMS page, if not navigate
        current_url = driver.current_url
        if "live/my_sms" not in current_url:
            print("[*] Navigating to Live SMS page...")
            driver.get(config.SMS_URL)
            time.sleep(5)
        
        # Wait for the live SMS table to load
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "LiveTestSMS"))
        )
        
        # Get all SMS rows from the table
        rows = driver.find_elements(By.CSS_SELECTOR, "#LiveTestSMS tr")
        
        if not rows:
            print("[⚠️] No SMS rows found in table.")
            return
        
        new_count = 0
        
        for row in rows:
            try:
                # Extract data from each column in the row
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) < 5:
                    continue
                
                # Column 0: Live SMS (Country + Number)
                country_number_cell = cells[0]
                country_number_text = country_number_cell.text.strip()
                
                # Extract phone number from the text
                number = extract_phone_number_from_text(country_number_text)
                
                # Column 1: SID (Sender)
                sender_cell = cells[1]
                sender = sender_cell.text.strip() if sender_cell.text else "Unknown"
                
                # Column 4: Message content
                message_cell = cells[4]
                message = message_cell.text.strip()
                
                # Skip if no message or already processed
                if not message:
                    continue
                    
                # Create unique identifier for this message
                message_id = f"{sender}_{number}_{message[:50]}"
                if message_id in last_messages:
                    continue
                
                last_messages.add(message_id)
                new_count += 1
                
                # Process and send the message in separate thread
                executor.submit(process_single_sms, sender, number, message)
                
            except StaleElementReferenceException:
                continue
            except Exception as e:
                print(f"[⚠️] Error processing row: {e}")
                continue
        
        if new_count > 0:
            print(f"[ℹ️] Processed {new_count} new messages.")
        
    except Exception as e:
        print(f"[❌] Failed to extract SMS: {e}")

def process_single_sms(sender, number, message):
    """Process a single SMS in separate thread"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        otp_code = extract_otp(message)
        
        # Detect country from phone number
        country_name, country_flag = detect_country(number)
        masked_number = mask_number(number)
        
        # Format the message for Telegram
        formatted = (
            f"🔥 **New SMS Captured! From {sender} {country_flag}**\n\n"
            f"🕒 **Time:** `{timestamp}`\n"
            f"{country_flag} **Country:** `{country_name}`\n"
            f"🌐 **Sender:** `{sender}`\n"
            f"📞 **Number:** `{masked_number}`\n"
            f"🔐 **OTP:** `{otp_code}`\n\n"
            f"💬 **Full Message:**\n"
            f"```{message}```"
        )
        
        # Send to Telegram
        if send_message(formatted, is_log=False):
            print(f"[✅] Sent: {sender} - OTP: {otp_code}")
        else:
            print(f"[❌] Failed to send: {sender}")
        
        # Small delay between messages
        time.sleep(1)
        
    except Exception as e:
        print(f"[💥] Error processing SMS: {e}")

def check_session_active(driver):
    """Check if user session is still active"""
    try:
        current_url = driver.current_url
        if "/portal" in current_url:
            return True
        else:
            return False
    except:
        return False

def main():
    global monitoring_active, refresh_pattern_index
    
    refresh_pattern_index = 0
    
    with SB(uc=True, headed=True, incognito=True) as sb:
        try:
            # Wait for manual login and auto-redirect
            if not wait_for_login_and_redirect(sb):
                send_monitoring_stopped("Login failed")
                return
            
            print("\n[*] iVASMS SMS Monitor is now ACTIVE")
            print("[*] Monitoring Live SMS every 10 seconds")
            print("[*] Auto-refresh every 5 minutes")
            print("[*] Press Ctrl+C to stop\n")
            
            error_count = 0
            last_refresh = datetime.now()
            next_refresh_interval = get_next_refresh_time()
            
            while error_count < config.MAX_ERRORS and monitoring_active:
                try:
                    current_time = datetime.now()
                    time_until_refresh = next_refresh_interval - (current_time - last_refresh).total_seconds()
                    
                    # Send refresh warning 10 seconds before refresh
                    if 0 < time_until_refresh <= 10:
                        send_refresh_warning()
                        time.sleep(time_until_refresh)
                    
                    # Auto-refresh based on pattern
                    if (current_time - last_refresh).total_seconds() > next_refresh_interval:
                        print(f"[🔄] Scheduled refresh triggered after {next_refresh_interval} seconds")
                        
                        if safe_refresh_with_advanced_captcha(sb.driver):
                            WebDriverWait(sb.driver, 20).until(
                                EC.presence_of_element_located((By.ID, "LiveTestSMS"))
                            )
                            last_refresh = current_time
                            next_refresh_interval = get_next_refresh_time()
                            print(f"[✅] Page refreshed successfully at {current_time.strftime('%H:%M:%S')}")
                        else:
                            print("[❌] Page refresh failed, trying to recover...")
                            handle_captcha_protection(sb, config.SMS_URL, "Recovery Refresh")
                            next_refresh_interval = REFRESH_PATTERN[0]
                    
                    # Check session activity
                    if not check_session_active(sb.driver):
                        print("[❌] Session expired. Please login again.")
                        send_monitoring_stopped("Session expired")
                        if not wait_for_login_and_redirect(sb):
                            break
                    
                    # Extract SMS
                    extract_ivasms_sms(sb.driver)
                    
                    error_count = 0
                    time.sleep(10)  # Check every 10 seconds
                    
                except KeyboardInterrupt:
                    print("\n[🛑] Stopped by user.")
                    send_monitoring_stopped("Manual interruption by user")
                    break
                except Exception as e:
                    error_count += 1
                    print(f"[❌] Main loop error ({error_count}/{config.MAX_ERRORS}): {e}")
                    
                    # Enhanced CAPTCHA-related error handling
                    error_str = str(e).lower()
                    if any(keyword in error_str for keyword in ["captcha", "cloudflare", "challenge", "security", "verification"]):
                        print("[🛡️] CAPTCHA-related error detected, attempting advanced recovery...")
                        handle_captcha_protection(sb, sb.driver.current_url, "Error Recovery")
                        human_like_delay(10, 15)
                    
                    time.sleep(5)
                    
        except Exception as e:
            print(f"[💥] Fatal error: {e}")
            send_monitoring_stopped(f"Fatal error: {str(e)}")
    
    monitoring_active = False
    executor.shutdown(wait=False)
    print("[*] iVASMS SMS Monitor stopped.")

if __name__ == "__main__":
    main()
