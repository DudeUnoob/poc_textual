import { createClient } from 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.57.4/+esm';
const config = JSON.parse(document.getElementById('supabase-config').textContent);
const client = createClient(config.url, config.publishableKey, {
  auth: {persistSession:false, autoRefreshToken:false, detectSessionInUrl:false},
});
const form = document.getElementById('auth-form');
const submit = document.getElementById('auth-submit');
const email = document.getElementById('email');
const password = document.getElementById('password');
const toggle = document.getElementById('toggle-register');
const resend = document.getElementById('resend-verify');
const notice = document.getElementById('auth-notice');
const error = document.getElementById('auth-error');
const domains = config.allowedDomains || ['utexas.edu'];
const callback = `${location.origin}/login`;
const show = (message, failed=false) => {
  error.hidden = !failed; notice.hidden = failed;
  (failed ? error : notice).textContent = message;
};
// Supabase's registration hook and the backend enforce the access allowlist.
// Keep private account exceptions out of browser configuration.
const allowed = () => /^[^@\s]+@[^@\s]+$/.test(email.value.trim());
const checked = result => { if (result.error) throw result.error; return result.data; };
const hash = new URLSearchParams(location.hash.slice(1));
const recovery = ['recovery', 'invite'].includes(hash.get('type'));
// Remove credentials from URL before any navigation, including failure paths.
history.replaceState(null, '', location.pathname);
let recoveryReady = false;
if (recovery && hash.get('access_token') && hash.get('refresh_token')) {
  try {
    checked(await client.auth.setSession({access_token:hash.get('access_token'), refresh_token:hash.get('refresh_token')}));
    recoveryReady = true; form.dataset.mode = 'recovery'; email.closest('label').hidden = true;
    email.required = false; submit.textContent = 'Set new password'; password.autocomplete = 'new-password';
    toggle.hidden = true; document.getElementById('send-reset').hidden = true;
    show('Choose a new password. You will sign in again after saving it.');
  } catch { show('This reset link has expired. Request a new password reset.', true); }
} else if (hash.get('error_description')) show(hash.get('error_description'), true);
else if (hash.get('type') === 'signup') show('Email verified. Sign in to continue.');

form.addEventListener('submit', async event => {
  event.preventDefault(); submit.disabled = true;
  try {
    if (form.dataset.mode === 'recovery' && recoveryReady) {
      checked(await client.auth.updateUser({password:password.value}));
      await client.auth.signOut({scope:'global'});
      location.assign('/login'); return;
    }
    if (!allowed()) throw new Error('Enter a valid approved email address.');
    if (form.dataset.mode === 'register') {
      checked(await client.auth.signUp({email:email.value.trim(), password:password.value, options:{emailRedirectTo:callback}}));
      show('Check your email to verify your account, then sign in.');
      form.dataset.mode = 'signin'; submit.textContent = 'Sign in'; toggle.textContent = 'Create an account';
      return;
    }
    const {session, user} = checked(await client.auth.signInWithPassword({email:email.value.trim(), password:password.value}));
    if (!session || !user.email_confirmed_at) throw new Error('Verify your email first.');
    const response = await fetch('/session', {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-CSRF-Token':config.csrfToken},
      body:JSON.stringify({idToken:session.access_token, csrf_token:config.csrfToken})});
    if (!response.ok) throw new Error((await response.json()).detail || 'Could not start session.');
    // Do not call signOut: that would revoke the server's new cookie session.
    location.assign('/');
  } catch (err) { show(err.message || 'Authentication failed.', true); resend.hidden = false; }
  finally { submit.disabled = false; }
});
toggle.addEventListener('click', () => {
  const register = form.dataset.mode !== 'register';
  form.dataset.mode = register ? 'register' : 'signin';
  submit.textContent = register ? 'Create account' : 'Sign in';
  toggle.textContent = register ? 'Have an account? Sign in' : 'Create an account';
  password.autocomplete = register ? 'new-password' : 'current-password';
});
document.getElementById('send-reset').addEventListener('click', async () => {
  if (!allowed()) return show('Enter your email first.', true);
  try { await client.auth.resetPasswordForEmail(email.value.trim(), {redirectTo:`${callback}?recovery=1`}); } catch {}
  show('If that address has an account, a reset email was sent.');
});
resend.addEventListener('click', async () => {
  if (!allowed()) return show('Enter your email first.', true);
  try { await client.auth.resend({type:'signup', email:email.value.trim(), options:{emailRedirectTo:callback}}); } catch {}
  show('If verification is needed, an email was sent.');
});
