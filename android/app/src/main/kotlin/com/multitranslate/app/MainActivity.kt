package com.multitranslate.app

import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.text.InputType
import android.view.ViewGroup
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.Toast

/**
 * MultiTranslate phone app: a WebView shell for the MultiTranslate server running on the laptop
 * (start it with MultiTranslate.bat; the page header shows the address to enter here).
 * Files chosen on the phone are uploaded to the laptop; results download back to the phone.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private val prefs by lazy { getSharedPreferences("mt", Context.MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            useWideViewPort = true
            loadWithOverviewMode = true
        }
        web.webViewClient = MainClient()
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "*/*"
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }
                startActivityForResult(Intent.createChooser(intent, "Choose files"), REQ_FILE)
                return true
            }
        }
        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
            val req = DownloadManager.Request(Uri.parse(url))
                .setMimeType(mimeType)
                .addRequestHeader("User-Agent", userAgent)
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
            (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
            Toast.makeText(this, "Downloading $name to Downloads", Toast.LENGTH_SHORT).show()
        }
        val saved = prefs.getString(KEY_URL, null)
        web.loadUrl(saved ?: DEFAULT_URL)                   // website by default; "Change address" for a laptop
    }

    /** Handles the offline page's two pseudo-links and shows that page whenever the laptop is unreachable. */
    private inner class MainClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            when (request.url.toString()) {
                "mt://retry" -> { web.loadUrl(prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL); return true }
                "mt://settings" -> { askServer(prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL); return true }
            }
            return false
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            // Free hosting sleeps when idle and takes ~30-60 s to wake: retry quietly before giving up.
            if (retries < MAX_RETRIES) {
                retries++
                showWaking(retries)
                web.postDelayed({ web.loadUrl(request.url.toString()) }, RETRY_MS)
            } else {
                retries = 0
                showOffline(error.description?.toString() ?: "connection failed")
            }
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (!url.startsWith("http://offline.local")) retries = 0
        }
    }

    private var retries = 0

    private fun showWaking(attempt: Int) {
        val html = """
            <html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head>
            <body style='font-family:sans-serif;padding:28px;background:#FBFAF6;color:#17203F;text-align:center'>
            <h2 style='margin-top:120px'>Starting MultiTranslate&hellip;</h2>
            <p style='color:#6B7080'>The server is waking up. This can take up to a minute the first time. ($attempt/$MAX_RETRIES)</p>
            </body></html>
        """.trimIndent()
        web.loadDataWithBaseURL("http://offline.local/waking", html, "text/html", "utf-8", null)
    }

    private fun askServer(current: String) {
        val input = EditText(this).apply {
            setText(current)
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setSelection(text.length)
        }
        AlertDialog.Builder(this)
            .setTitle("Server address")
            .setMessage("Default is the MultiTranslate website. To use your own laptop instead, run MultiTranslate there and enter its address (shown in its black window), e.g. http://192.168.1.14:5055.")
            .setView(input)
            .setCancelable(false)
            .setPositiveButton("Connect") { _, _ ->
                var url = input.text.toString().trim()
                if (!url.startsWith("http")) url = "http://$url"
                prefs.edit().putString(KEY_URL, url).apply()
                web.loadUrl(url)
            }
            .show()
    }

    private fun showOffline(reason: String) {
        val url = prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL
        val html = """
            <html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head>
            <body style='font-family:sans-serif;padding:28px;background:#FBFAF6;color:#17203F'>
            <h2 style='margin-top:40px'>Could not connect</h2>
            <p style='color:#6B7080'>$reason</p>
            <p>Check your internet connection, then retry. Address: <code>$url</code></p>
            <p style='margin-top:28px'>
              <a href='mt://retry' style='display:inline-block;padding:14px 20px;background:#17203F;color:#fff;border-radius:10px;text-decoration:none;font-weight:bold'>Retry</a>
              &nbsp;&nbsp;<a href='mt://settings' style='display:inline-block;padding:14px 20px;background:#E8891D;color:#fff;border-radius:10px;text-decoration:none;font-weight:bold'>Change address</a>
            </p></body></html>
        """.trimIndent()
        web.loadDataWithBaseURL("http://offline.local/", html, "text/html", "utf-8", null)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != REQ_FILE) return
        val uris = mutableListOf<Uri>()
        if (resultCode == RESULT_OK && data != null) {
            data.clipData?.let { clip -> for (i in 0 until clip.itemCount) uris += clip.getItemAt(i).uri }
            data.data?.let { if (uris.isEmpty()) uris += it }
        }
        fileCallback?.onReceiveValue(if (uris.isEmpty()) null else uris.toTypedArray())
        fileCallback = null
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    companion object {
        private const val REQ_FILE = 41
        private const val KEY_URL = "server_url"
        private const val DEFAULT_URL = "https://multitranslate.onrender.com/app"   // hosted website
        private const val MAX_RETRIES = 10
        private const val RETRY_MS = 6000L
    }
}
