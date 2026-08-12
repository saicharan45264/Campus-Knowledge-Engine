export function getToken() {
  return localStorage.getItem('access_token') || '';
}

export function getRole() {
  return localStorage.getItem('user_role');
}

export function getUsername() {
  return localStorage.getItem('username');
}

export function setAuth(token, role, username) {
  localStorage.setItem('access_token', token);
  localStorage.setItem('user_role', role);
  localStorage.setItem('username', username);
}

export function logout() {
  localStorage.removeItem('access_token');
  localStorage.removeItem('user_role');
  localStorage.removeItem('username');
  window.location.href = 'login.html';
}

export function requireRole(expectedRole) {
  const token = getToken();
  const role = getRole();
  if (!token || role !== expectedRole) {
    window.location.href = 'login.html';
  }
}
