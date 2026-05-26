"""
LinkedIn Apply Button Debugging Script
Finds the external Apply button on LinkedIn job pages
"""

import time
import sys
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

# LinkedIn job URL with external Apply button
JOB_URL = "https://www.linkedin.com/jobs/view/4324889941/"

def setup_driver():
    """Initialize Selenium WebDriver with user profile"""
    chrome_options = Options()
    # Don't use headless - we need to see the page
    # chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    # Use existing Chrome profile (so you stay logged in)
    # This uses your default Chrome profile with saved logins
    chrome_options.add_argument("--user-data-dir=C:\\Users\\f_beh\\AppData\\Local\\Google\\Chrome\\User Data")
    chrome_options.add_argument("--profile-directory=Default")

    driver = webdriver.Chrome(options=chrome_options)
    return driver

def wait_for_page_load(driver, timeout=10):
    """Wait for page to load"""
    wait = WebDriverWait(driver, timeout)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(3)  # Additional wait for dynamic content

def test_selector(driver, selector_dict, selector_type="xpath"):
    """Test a single selector"""
    try:
        if selector_type == "xpath":
            element = driver.find_element(By.XPATH, selector_dict['selector'])
        elif selector_type == "css":
            element = driver.find_element(By.CSS_SELECTOR, selector_dict['selector'])
        elif selector_type == "class":
            element = driver.find_element(By.CLASS_NAME, selector_dict['selector'])

        is_visible = element.is_displayed()
        is_enabled = element.is_enabled()
        text = element.text
        tag = element.tag_name

        result = {
            'name': selector_dict['name'],
            'selector': selector_dict['selector'],
            'found': True,
            'visible': is_visible,
            'enabled': is_enabled,
            'text': text,
            'tag': tag,
            'html': element.get_attribute('outerHTML')[:200]
        }

        return result
    except Exception as e:
        return {
            'name': selector_dict['name'],
            'selector': selector_dict['selector'],
            'found': False,
            'error': str(e)
        }

def find_all_buttons(driver):
    """Find ALL buttons on the page and their info"""
    print("\n" + "="*80)
    print("ALL BUTTONS ON PAGE")
    print("="*80)

    buttons = driver.find_elements(By.TAG_NAME, "button")
    print(f"Found {len(buttons)} buttons total\n")

    for i, button in enumerate(buttons):
        try:
            text = button.text
            visible = button.is_displayed()
            classes = button.get_attribute('class')
            aria_label = button.get_attribute('aria-label')
            data_test = button.get_attribute('data-test-id')

            print(f"Button {i}:")
            print(f"  Text: {text}")
            print(f"  Visible: {visible}")
            print(f"  Classes: {classes}")
            print(f"  aria-label: {aria_label}")
            print(f"  data-test-id: {data_test}")
            print()
        except Exception as e:
            print(f"Button {i}: Error reading - {e}\n")

def find_apply_buttons(driver):
    """Test multiple selectors for Apply button"""

    selectors = [
        # Easy Apply buttons
        {
            'name': 'Easy Apply Button (text)',
            'selector': "//button[contains(text(), 'Easy Apply')]"
        },
        # External Apply buttons
        {
            'name': 'Apply Button (text)',
            'selector': "//button[contains(text(), 'Apply') and not(contains(text(), 'Easy'))]"
        },
        {
            'name': 'Apply Button (aria-label)',
            'selector': "//button[contains(@aria-label, 'Apply')]"
        },
        {
            'name': 'Apply Button (data-test-id)',
            'selector': "//button[@data-test-id='apply-button']"
        },
        {
            'name': 'Apply Button (class jobs-apply)',
            'selector': "//button[contains(@class, 'jobs-apply')]"
        },
        {
            'name': 'Primary Button (Apply)',
            'selector': "//button[contains(@class, 'button--primary')][contains(., 'Apply')]"
        },
        {
            'name': 'Button with href (external)',
            'selector': "//button[contains(@onclick, 'apply')]"
        },
        {
            'name': 'Apply (any case)',
            'selector': "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]"
        },
        {
            'name': 'Apply in job card',
            'selector': "//div[@class='jobs-details-top-card__job-title']//following::button[contains(text(), 'Apply')][1]"
        },
    ]

    print("\n" + "="*80)
    print("TESTING SELECTORS FOR APPLY BUTTON")
    print("="*80 + "\n")

    results = []
    for selector in selectors:
        result = test_selector(driver, selector)
        results.append(result)

        if result['found']:
            print(f"✅ FOUND: {result['name']}")
            print(f"   Selector: {result['selector']}")
            print(f"   Visible: {result['visible']}")
            print(f"   Enabled: {result['enabled']}")
            print(f"   Text: {result['text']}")
            print(f"   Tag: {result['tag']}")
            print()
        else:
            print(f"❌ NOT FOUND: {result['name']}")
            print(f"   Selector: {result['selector']}")
            print()

    return results

def analyze_page_structure(driver):
    """Analyze page HTML structure"""
    print("\n" + "="*80)
    print("PAGE STRUCTURE ANALYSIS")
    print("="*80 + "\n")

    # Get page title
    title = driver.find_element(By.TAG_NAME, "title").text
    print(f"Page Title: {title}\n")

    # Look for job details section
    try:
        job_title = driver.find_element(By.XPATH, "//h1").text
        print(f"Job Title: {job_title}\n")
    except:
        pass

    # Find all links/buttons that might be Apply
    print("Looking for potential Apply button elements...\n")

    # Check for <a> tags with apply
    links = driver.find_elements(By.XPATH, "//a[contains(text(), 'Apply') or contains(@href, 'apply')]")
    if links:
        print(f"Found {len(links)} <a> tags with 'Apply':")
        for link in links[:5]:
            print(f"  - {link.text} | href={link.get_attribute('href')}")
        print()

    # Check page source for "Apply" text
    page_source = driver.page_source
    apply_count = page_source.count("Apply")
    print(f"'Apply' appears {apply_count} times in page source")

    # Check for form or external links
    forms = driver.find_elements(By.TAG_NAME, "form")
    print(f"Found {len(forms)} <form> elements")

def main():
    """Main debugging flow"""
    driver = None

    try:
        print(f"Starting debugging for: {JOB_URL}")
        print("="*80)

        # Setup
        driver = setup_driver()
        driver.get(JOB_URL)

        # Wait for page to load
        print("Waiting for page to load...")
        print("If you're not logged in, please log in to LinkedIn now...")
        wait_for_page_load(driver)

        # Check if logged in
        try:
            driver.find_element(By.XPATH, "//button[contains(text(), 'Easy Apply')]")
            print("✅ Logged in - buttons visible\n")
        except:
            print("⚠️  Buttons not visible - you may need to log in\n")

        # Analyze structure
        analyze_page_structure(driver)

        # Find all buttons
        find_all_buttons(driver)

        # Test selectors
        results = find_apply_buttons(driver)

        # Summary
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)

        found = [r for r in results if r['found']]
        if found:
            print(f"\n✅ Found {len(found)} working selector(s):\n")
            for r in found:
                print(f"  📍 {r['name']}")
                print(f"     Selector: {r['selector']}")
                print(f"     Visible: {r['visible']}, Enabled: {r['enabled']}")
                print()
        else:
            print("\n❌ No Apply button selectors found!")
            print("This may mean:")
            print("  1. The page hasn't loaded the job details yet")
            print("  2. The Apply button is in an iframe")
            print("  3. The selector names have changed")
            print("  4. The button loads dynamically after scroll")
            print()

        # Keep browser open for inspection
        print("\n" + "="*80)
        print("Browser remains open for manual inspection.")
        print("Press Enter to close...")
        print("="*80)
        input()

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    main()
