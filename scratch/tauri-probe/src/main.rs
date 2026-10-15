use webkit2gtk::{WebViewExt, WebContextExt, WebsiteDataManagerExt};
fn main() {
    tauri::Builder::default().setup(|app| {
        let window = tauri::WebviewWindowBuilder::new(app,"probe",tauri::WebviewUrl::App("index.html".into()))
            .title("PlaudVault synthetic probe").incognito(true)
            .data_directory(std::env::temp_dir().join("pv-probe-webdata"))
            .build()?;
        window.with_webview(|webview| {
            let inner=webview.inner();
            let context=inner.context().unwrap();
            println!("context ephemeral={} sandbox={}",context.is_ephemeral(),context.is_sandbox_enabled());
            let data=context.website_data_manager().unwrap();
            let mut proxy=webkit2gtk::NetworkProxySettings::new(Some("http://127.0.0.1:9"), &["localhost", "127.0.0.1"]);
            if std::env::var_os("PV_PROBE_NO_PROXY").is_none() { data.set_network_proxy_settings(webkit2gtk::NetworkProxyMode::Custom,Some(&mut proxy)); }
        })?;
        Ok(())
    }).run(tauri::generate_context!()).expect("probe failed");
}
