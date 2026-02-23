/**
 * app.js — Price Drop Notifier frontend
 *
 * !! IMPORTANT !!
 * Replace the API_BASE_URL below with the output from `sam deploy`.
 * You can find it by running: aws cloudformation describe-stacks \
 *   --stack-name price-drop-notifier --query "Stacks[0].Outputs"
 */
const API_BASE_URL = 'https://jw6slnyms9.execute-api.us-east-1.amazonaws.com/Prod';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const form        = document.getElementById('subscribeForm');
const inputUrl    = document.getElementById('inputUrl');
const inputName   = document.getElementById('inputName');
const inputEmail  = document.getElementById('inputEmail');
const fieldUrl    = document.getElementById('fieldUrl');
const fieldEmail  = document.getElementById('fieldEmail');
const errorUrl    = document.getElementById('errorUrl');
const errorEmail  = document.getElementById('errorEmail');
const submitBtn   = document.getElementById('submitBtn');
const alertError  = document.getElementById('alertError');
const alertErrTxt = document.getElementById('alertErrorText');

const stepForm     = document.getElementById('stepForm');
const stepChecking = document.getElementById('stepChecking');
const stepSuccess  = document.getElementById('stepSuccess');
const checkingText = document.getElementById('checkingText');

const previewName  = document.getElementById('previewName');
const previewPrice = document.getElementById('previewPrice');
const trackAnother = document.getElementById('trackAnotherBtn');

// ── Helpers ───────────────────────────────────────────────────────────────────
function showStep(step) {
  [stepForm, stepChecking, stepSuccess].forEach(el => {
    el.hidden = (el !== step);
  });
}

function setFieldError(fieldEl, errorEl, msg) {
  if (msg) {
    fieldEl.classList.add('field--error');
    errorEl.textContent = msg;
  } else {
    fieldEl.classList.remove('field--error');
    errorEl.textContent = '';
  }
}

function showGlobalError(msg) {
  alertErrTxt.textContent = msg;
  alertError.hidden = false;
}

function hideGlobalError() {
  alertError.hidden = true;
  alertErrTxt.textContent = '';
}

function formatPrice(price, currency = 'USD') {
  const symbols = { USD: '$', GBP: '£', EUR: '€' };
  const sym = symbols[currency] ?? '$';
  return `${sym}${price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// ── Validation ────────────────────────────────────────────────────────────────
function validateForm() {
  let valid = true;

  // URL
  const url = inputUrl.value.trim();
  if (!url) {
    setFieldError(fieldUrl, errorUrl, 'Please enter a product URL.');
    valid = false;
  } else if (!/^https?:\/\/.+\..+/i.test(url)) {
    setFieldError(fieldUrl, errorUrl, 'Please enter a valid URL starting with http:// or https://');
    valid = false;
  } else {
    setFieldError(fieldUrl, errorUrl, '');
  }

  // Email
  const email = inputEmail.value.trim();
  if (!email) {
    setFieldError(fieldEmail, errorEmail, 'Please enter your email address.');
    valid = false;
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    setFieldError(fieldEmail, errorEmail, 'Please enter a valid email address.');
    valid = false;
  } else {
    setFieldError(fieldEmail, errorEmail, '');
  }

  return valid;
}

// Clear inline errors on input
inputUrl.addEventListener('input', () => setFieldError(fieldUrl, errorUrl, ''));
inputEmail.addEventListener('input', () => setFieldError(fieldEmail, errorEmail, ''));

// ── Form submit ───────────────────────────────────────────────────────────────
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideGlobalError();

  if (!validateForm()) return;

  const url         = inputUrl.value.trim();
  const email       = inputEmail.value.trim().toLowerCase();
  const productName = inputName.value.trim();

  // Loading state
  submitBtn.disabled = true;
  submitBtn.classList.add('btn--loading');
  showStep(stepChecking);
  checkingText.textContent = 'Checking product page…';

  try {
    const resp = await fetch(`${API_BASE_URL}/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, email, productName }),
    });

    const data = await resp.json();

    if (!resp.ok) {
      // Server returned an error
      const msg = data?.error || `Server error (${resp.status}). Please try again.`;

      // Switch back to form and show error
      showStep(stepForm);
      showGlobalError(msg);

      // If it's a field-specific error, highlight the relevant field too
      if (msg.toLowerCase().includes('url') || msg.toLowerCase().includes('price')) {
        setFieldError(fieldUrl, errorUrl, msg);
      } else if (msg.toLowerCase().includes('email')) {
        setFieldError(fieldEmail, errorEmail, msg);
      }
      return;
    }

    // ── Success ───────────────────────────────────────────────────────────────
    const { product } = data;
    const productPreview = document.getElementById('productPreview');
    const successSubtitle = document.querySelector('#stepSuccess .card__subtitle');

    if (product && product.price) {
      previewName.textContent  = product.name || 'Product';
      previewPrice.textContent = formatPrice(product.price, product.currency);
      productPreview.hidden = false;
      successSubtitle.textContent = 'Check your inbox for a confirmation email with current pricing.';
    } else {
      productPreview.hidden = true;
      successSubtitle.textContent = "You're subscribed! We'll email you the moment the price drops — check your inbox for a confirmation.";
    }

    if (!data.emailSent) {
      successSubtitle.textContent = 'Subscribed! Note: email delivery is not yet configured.';
    }

    showStep(stepSuccess);

  } catch (err) {
    // Network error
    showStep(stepForm);
    showGlobalError('Could not reach the server. Check your connection and try again.');
    console.error(err);
  } finally {
    submitBtn.disabled = false;
    submitBtn.classList.remove('btn--loading');
  }
});

// ── "Track another" button ────────────────────────────────────────────────────
trackAnother.addEventListener('click', () => {
  form.reset();
  hideGlobalError();
  [fieldUrl, fieldEmail].forEach(f => f.classList.remove('field--error'));
  inputName.value = '';
  [errorUrl, errorEmail].forEach(e => (e.textContent = ''));
  previewName.textContent  = '';
  previewPrice.textContent = '';
  showStep(stepForm);
});
