import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, tap } from 'rxjs';

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: {
    id: number;
    nombre_completo: string;
    email: string;
    rol: string;
  };
}

@Injectable({
  providedIn: 'root'
})
export class AuthService {

  // URL del nuevo backend Django unificado (Chem Movid con Django)
  private apiUrl = 'http://127.0.0.1:8000';

  constructor(private http: HttpClient) {}

  login(data: { correo: string; contrasena: string }): Observable<LoginResponse> {
    return this.http.post<LoginResponse>(`${this.apiUrl}/api/auth/login/`, data).pipe(
      tap(res => {
        this.saveToken(res.access_token);
        localStorage.setItem('refresh_token', res.refresh_token);
        localStorage.setItem('user', JSON.stringify(res.user));
      })
    );
  }

  logout(): Observable<any> {
    return this.http.post(`${this.apiUrl}/api/auth/logout/`, {}, {
      headers: this.getAuthHeaders()
    });
  }

  saveToken(token: string): void {
    localStorage.setItem('token', token);
  }

  getToken(): string | null {
    return localStorage.getItem('token');
  }

  isAuthenticated(): boolean {
    return !!this.getToken();
  }

  getCurrentUser(): LoginResponse['user'] | null {
    const user = localStorage.getItem('user');
    return user ? JSON.parse(user) : null;
  }

  getRol(): string | null {
    return this.getCurrentUser()?.rol ?? null;
  }

  getAuthHeaders(): HttpHeaders {
    return new HttpHeaders({
      'Authorization': `Bearer ${this.getToken()}`,
      'Content-Type': 'application/json'
    });
  }

  clearSession(): void {
    localStorage.removeItem('token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
  }
}
