#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            if app.get_webview_window("approval-widget").is_none() {
                let _ = WebviewWindowBuilder::new(
                    app,
                    "approval-widget",
                    WebviewUrl::App("index.html?widget=approval".into()),
                )
                .title("Approval Widget")
                .inner_size(400.0, 620.0)
                .min_inner_size(320.0, 320.0)
                .resizable(true)
                .decorations(false)
                .always_on_top(true)
                .focused(false)
                .skip_taskbar(false)
                .visible(true)
                .build()?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running agente desktop")
}
