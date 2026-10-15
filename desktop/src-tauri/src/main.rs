#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[cfg(not(target_os = "linux"))]
compile_error!("PlaudVault Desktop requires Linux for its verified sandbox and RAM profile.");

use std::io::{BufRead, Read};
use std::sync::{Arc, Mutex};
use tauri::Url;
use webkit2gtk::{WebContextExt, WebViewExt, WebsiteDataManagerExt};

#[derive(Clone, Debug, PartialEq)]
struct LocalOrigin {
    port: u16,
}

fn login_url(raw: &str) -> Result<(Url, LocalOrigin), &'static str> {
    if raw.len() > 8192 || raw.chars().any(char::is_control) {
        return Err("invalid session address");
    }
    let url = Url::parse(raw).map_err(|_| "invalid session address")?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || url.path() != "/login"
    {
        return Err("invalid session address");
    }
    let port = url
        .port()
        .filter(|p| *p > 0)
        .ok_or("invalid session port")?;
    let pairs: Vec<_> = url.query_pairs().collect();
    if pairs.len() != 1 || pairs[0].0 != "t" || pairs[0].1.is_empty() {
        return Err("invalid session token");
    }
    Ok((url, LocalOrigin { port }))
}

fn navigation_allowed(url: &Url, origin: Option<&LocalOrigin>) -> bool {
    match origin {
        Some(origin) => {
            url.scheme() == "http"
                && url.host_str() == Some("127.0.0.1")
                && url.port() == Some(origin.port)
                && url.username().is_empty()
                && url.password().is_none()
        }
        None => {
            url.scheme() == "tauri"
                && url.host_str() == Some("localhost")
                && matches!(url.path(), "/" | "/index.html")
                && url.query().is_none()
                && url.fragment().is_none()
        }
    }
}

fn main() {
    let allowed_origin = Arc::new(Mutex::new(None::<LocalOrigin>));
    tauri::Builder::default()
        .setup(move |app| {
            let navigation_origin = Arc::clone(&allowed_origin);
            let window = tauri::WebviewWindowBuilder::new(
                app,
                "vault",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("PlaudVault")
            .inner_size(1280.0, 820.0)
            .min_inner_size(860.0, 600.0)
            .incognito(true)
            .data_directory(std::env::temp_dir().join("webdata"))
            .on_navigation(move |url| {
                navigation_origin
                    .lock()
                    .map(|origin| navigation_allowed(url, origin.as_ref()))
                    .unwrap_or(false)
            })
            .build()?;

            let handle = app.handle().clone();
            let navigate_window = window.clone();
            let origin = Arc::clone(&allowed_origin);
            window.with_webview(move |webview| {
                let Some(context) = webview.inner().context() else {
                    handle.exit(1);
                    return;
                };
                // The launcher forces sandbox before WebKit creates any child process.
                // Refuse the session instead of changing the context after startup.
                if !context.is_ephemeral() || !context.is_sandbox_enabled() {
                    eprintln!("No se cumplen las condiciones de la sesión protegida.");
                    handle.exit(1);
                    return;
                }
                let Some(data) = context.website_data_manager() else {
                    handle.exit(1);
                    return;
                };
                let mut proxy = webkit2gtk::NetworkProxySettings::new(
                    Some("http://127.0.0.1:9"),
                    &["localhost", "127.0.0.1"],
                );
                data.set_network_proxy_settings(
                    webkit2gtk::NetworkProxyMode::Custom,
                    Some(&mut proxy),
                );
                std::thread::spawn(move || {
                    let mut line = String::new();
                    let result = std::io::stdin().lock().take(8193).read_line(&mut line);
                    if result.is_err() || !line.ends_with('\n') {
                        handle.exit(1);
                        return;
                    }
                    // Never print the address: its query contains a one-use credential.
                    let Ok((url, local)) = login_url(line.trim_end_matches(['\r', '\n'])) else {
                        eprintln!("La dirección de sesión local no es válida.");
                        handle.exit(1);
                        return;
                    };
                    let Ok(mut current) = origin.lock() else {
                        handle.exit(1);
                        return;
                    };
                    *current = Some(local);
                    drop(current);
                    if navigate_window.navigate(url).is_err() {
                        handle.exit(1);
                        return;
                    }
                    // EOF means the Python parent exited, including an abrupt kill.
                    let mut remaining = String::new();
                    let _ = std::io::stdin().lock().read_line(&mut remaining);
                    handle.exit(0);
                });
            })?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .unwrap_or_else(|_| {
            eprintln!("No se pudo iniciar PlaudVault Desktop.");
            std::process::exit(1);
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_a_local_login_with_one_token() {
        assert!(login_url("http://127.0.0.1:18991/login?t=synthetic").is_ok());
        for invalid in [
            "https://127.0.0.1:18991/login?t=x",
            "http://localhost:18991/login?t=x",
            "http://127.0.0.1/login?t=x",
            "http://127.0.0.1:0/login?t=x",
            "http://user@127.0.0.1:18991/login?t=x",
            "http://127.0.0.1:18991/other?t=x",
            "http://127.0.0.1:18991/login?t=",
            "http://127.0.0.1:18991/login?t=x&t=y",
            "http://127.0.0.1:18991/login?t=x#fragment",
            "http://127.0.0.1:18991/login?t=x\n",
        ] {
            assert!(login_url(invalid).is_err());
        }
    }

    #[test]
    fn navigation_stays_on_the_selected_origin() {
        let origin = LocalOrigin { port: 18991 };
        assert!(navigation_allowed(
            &Url::parse("tauri://localhost/index.html").unwrap(),
            None
        ));
        assert!(!navigation_allowed(
            &Url::parse("http://127.0.0.1:18991/").unwrap(),
            None
        ));
        assert!(navigation_allowed(
            &Url::parse("http://127.0.0.1:18991/recordings").unwrap(),
            Some(&origin)
        ));
        for invalid in [
            "http://127.0.0.1:18992/",
            "https://example.org/",
            "http://localhost:18991/",
            "tauri://localhost/index.html",
            "http://user@127.0.0.1:18991/",
        ] {
            assert!(!navigation_allowed(
                &Url::parse(invalid).unwrap(),
                Some(&origin)
            ));
        }
    }
}
