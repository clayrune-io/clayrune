// One shared Details control and vocabulary for every browser sign-in branch.
export const SigninMethod = {
  labels: {google: 'Sign in with Google', phone_code: 'Phone code', browser_password: 'Username and password', other: 'Other sign-in'},
  html(value = 'other') {
    return `<label class="desk-v1-conn-add-field">How you sign in<select class="desk-v1-rules-textinput" data-signin-method>${Object.entries(this.labels).map(([id, label]) => `<option value="${id}"${id === value ? ' selected' : ''}>${label}</option>`).join('')}</select></label>`;
  },
  bind(root, change) {
    root.querySelector('[data-signin-method]')?.addEventListener('change', e => change(e.target.value));
  },
};
