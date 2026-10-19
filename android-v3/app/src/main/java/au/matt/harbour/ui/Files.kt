package au.matt.harbour.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import au.matt.harbour.data.ActionResult
import au.matt.harbour.data.FileEntry
import au.matt.harbour.data.FileListing
import au.matt.harbour.data.fileOp
import au.matt.harbour.data.listFiles
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** A move/copy waiting for the user to pick a destination folder. */
private data class Transfer(val op: String, val path: String, val name: String, val fromDir: String)

@Composable
fun FilesScreen(onPlay: ((String) -> Unit)? = null) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val scope = rememberCoroutineScope()

    var path by rememberSaveable { mutableStateOf("") }
    var listing by remember { mutableStateOf<FileListing?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    var focusName by remember { mutableStateOf<String?>(null) }
    var grabPending by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    var menuFor by remember { mutableStateOf<FileEntry?>(null) }
    var transfer by remember { mutableStateOf<Transfer?>(null) }
    var renaming by remember { mutableStateOf<FileEntry?>(null) }
    var deleting by remember { mutableStateOf<FileEntry?>(null) }
    var newFolder by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf<String?>(null) }

    val atRoot = path.isEmpty()
    val picking = transfer != null

    LaunchedEffect(path, reload) {
        error = null
        try {
            val l = gw.listFiles(path)
            val visible = if (transfer != null) l.items.filter { it.isDir } else l.items
            listing = l
            val idx = visible.indexOfFirst { it.name == focusName }.coerceAtLeast(0)
            if (visible.isNotEmpty()) {
                focusName = visible[idx].name
                listState.scrollToItem(idx)
            }
            grabPending = true
        } catch (e: Exception) {
            error = e.message
        }
    }

    fun open(dir: String) {
        focusName = null
        path = dir
    }

    fun goUp() {
        if (atRoot) return
        focusName = path.substringAfterLast('/')
        path = listing?.parent ?: ""
    }

    fun runOp(label: String, focusAfter: String?, block: suspend () -> ActionResult) {
        busy = label
        scope.launch {
            try {
                val r = block()
                toaster.show(r.message)
                focusName = focusAfter
                reload++
            } catch (e: Exception) {
                toaster.show("⚠ ${e.message}")
            } finally {
                busy = null
            }
        }
    }

    fun neighbourOf(e: FileEntry): String? {
        val items = listing?.items ?: return null
        val i = items.indexOfFirst { it.path == e.path }
        return items.getOrNull(i + 1)?.name ?: items.getOrNull(i - 1)?.name
    }

    BackHandler(enabled = !atRoot) { goUp() }
    BackHandler(enabled = atRoot && picking) { transfer = null }

    Column(
        Modifier
            .fillMaxSize()
            .padding(start = 48.dp, end = 48.dp, top = 30.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f)) {
                Text(
                    if (atRoot) "Shared folders" else path.substringAfterLast('/'),
                    color = HColors.Text, fontSize = 28.sp, fontWeight = FontWeight.Bold,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Text(
                    if (atRoot) "Synology File Station" else path,
                    color = HColors.TextDim, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            val t = transfer
            if (t != null) {
                if (!atRoot) {
                    TvButton(if (t.op == "move") "Move here" else "Copy here", {
                        val dest = path
                        transfer = null
                        runOp(if (t.op == "move") "Moving" else "Copying", t.name) {
                            gw.fileOp(t.op, paths = listOf(t.path), dest = dest)
                        }
                    }, style = "primary")
                }
                TvButton("Cancel", { transfer = null })
            } else if (!atRoot) {
                TvButton("New folder", { newFolder = true })
                TvButton("Up", { goUp() })
            }
        }

        val t = transfer
        if (t != null) {
            Spacer(Modifier.height(10.dp))
            Text(
                "${if (t.op == "move") "Moving" else "Copying"} “${t.name}” — open the destination folder, then choose " +
                    if (t.op == "move") "Move here." else "Copy here.",
                color = HColors.Accent, fontSize = 14.sp,
            )
        }
        Spacer(Modifier.height(14.dp))

        Box(Modifier.weight(1f)) {
            val l = listing
            val err = error
            when {
                err != null && l == null -> ErrorPanel(err, onRetry = { reload++ })
                l == null -> Loading()
                else -> {
                    val entries = if (picking) l.items.filter { it.isDir } else l.items
                    if (entries.isEmpty()) {
                        Text(
                            if (picking) "No sub-folders here." else "This folder is empty.",
                            color = HColors.TextDim, fontSize = 16.sp,
                            modifier = Modifier.padding(top = 20.dp),
                        )
                    }
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                        contentPadding = PaddingValues(top = 4.dp, bottom = 24.dp, start = 4.dp, end = 4.dp),
                    ) {
                        items(entries, key = { it.path }) { e ->
                            val fr = remember { FocusRequester() }
                            if (grabPending && e.name == focusName) {
                                LaunchedEffect(l) {
                                    delay(30)
                                    runCatching { fr.requestFocus() }
                                    grabPending = false
                                }
                            }
                            FileRow(
                                entry = e,
                                focusRequester = fr,
                                onClick = {
                                    when {
                                        e.isDir -> open(e.path)
                                        picking -> {}
                                        onPlay != null && isPlayable(e.name) -> onPlay(e.path)
                                        else -> menuFor = e
                                    }
                                },
                                onMenu = if (!picking && !atRoot) ({ menuFor = e }) else null,
                                onFocused = { focusName = e.name },
                            )
                        }
                    }
                }
            }
        }
        Text(
            if (picking) "OK: open folder   ·   Back: up a level"
            else "OK: open / play   ·   ☰ Menu button: move, copy, rename, delete   ·   Back: up a level",
            color = HColors.TextDim, fontSize = 12.sp,
            modifier = Modifier.padding(vertical = 10.dp),
        )
    }

    menuFor?.let { e ->
        FileMenu(
            entry = e,
            onDismiss = { menuFor = null },
            onOpen = {
                menuFor = null
                open(e.path)
            },
            onMove = {
                menuFor = null
                transfer = Transfer("move", e.path, e.name, path)
                focusName = if (e.isDir) e.name else null
                reload++
                toaster.show("Pick where to move “${e.name}”")
            },
            onCopy = {
                menuFor = null
                transfer = Transfer("copy", e.path, e.name, path)
                focusName = if (e.isDir) e.name else null
                reload++
                toaster.show("Pick where to copy “${e.name}”")
            },
            onRename = {
                menuFor = null
                renaming = e
            },
            onDelete = {
                menuFor = null
                deleting = e
            },
        )
    }
    renaming?.let { e ->
        TextInputDialog(
            title = "Rename “${e.name}”",
            initial = e.name,
            onDone = { newName ->
                renaming = null
                if (newName.isNotBlank() && newName != e.name) {
                    runOp("Renaming", newName.trim()) { gw.fileOp("rename", path = e.path, name = newName.trim()) }
                }
            },
            onDismiss = { renaming = null },
        )
    }
    if (newFolder) {
        TextInputDialog(
            title = "New folder in ${path.substringAfterLast('/')}",
            initial = "",
            onDone = { name ->
                newFolder = false
                if (name.isNotBlank()) {
                    runOp("Creating folder", name.trim()) { gw.fileOp("mkdir", path = path, name = name.trim()) }
                }
            },
            onDismiss = { newFolder = false },
        )
    }
    deleting?.let { e ->
        ConfirmDialog(
            title = "Delete “${e.name}”?",
            message = (if (e.isDir) "The folder and everything in it will be deleted. " else "") +
                "If the Recycle Bin is on for this shared folder you can restore it from DSM.",
            confirmLabel = "Delete",
            danger = true,
            onConfirm = {
                deleting = null
                runOp("Deleting", neighbourOf(e)) { gw.fileOp("delete", paths = listOf(e.path)) }
            },
            onDismiss = { deleting = null },
        )
    }
    busy?.let { BusyDialog("$it…") }
}

@Composable
private fun FileRow(
    entry: FileEntry,
    focusRequester: FocusRequester,
    onClick: () -> Unit,
    onMenu: (() -> Unit)?,
    onFocused: () -> Unit,
) {
    FocusBox(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .height(54.dp),
        focusedScale = 1.012f,
        borderWidth = 2.dp,
        onFocused = onFocused,
        onMenu = onMenu,
        focusRequester = focusRequester,
    ) { f ->
        Row(
            Modifier
                .fillMaxSize()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.6f))
                .padding(horizontal = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(fileIcon(entry), fontSize = 20.sp)
            Spacer(Modifier.width(14.dp))
            Text(entry.name, color = HColors.Text, fontSize = 16.sp, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (!entry.isDir) {
                Text(fmtSize(entry.size), color = HColors.TextDim, fontSize = 13.sp,
                    textAlign = TextAlign.End, modifier = Modifier.width(96.dp))
            }
            Text(fmtDate(entry.modified), color = HColors.TextDim, fontSize = 13.sp,
                textAlign = TextAlign.End, modifier = Modifier.width(120.dp))
        }
    }
}

@Composable
private fun FileMenu(
    entry: FileEntry,
    onDismiss: () -> Unit,
    onOpen: () -> Unit,
    onMove: () -> Unit,
    onCopy: () -> Unit,
    onRename: () -> Unit,
    onDelete: () -> Unit,
) {
    val first = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .widthIn(min = 320.dp, max = 460.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(HColors.Surface)
                .padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("${fileIcon(entry)}  ${entry.name}", color = HColors.Text, fontSize = 18.sp,
                fontWeight = FontWeight.Bold, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (!entry.isDir) {
                Text("${fmtSize(entry.size)}  ·  ${fmtDate(entry.modified)}", color = HColors.TextDim, fontSize = 13.sp)
            }
            Spacer(Modifier.height(4.dp))
            if (entry.isDir) {
                TvButton("Open", onOpen, Modifier.fillMaxWidth(), fill = true, focusRequester = first)
                TvButton("Move to…", onMove, Modifier.fillMaxWidth(), fill = true)
            } else {
                TvButton("Move to…", onMove, Modifier.fillMaxWidth(), fill = true, focusRequester = first)
            }
            TvButton("Copy to…", onCopy, Modifier.fillMaxWidth(), fill = true)
            TvButton("Rename", onRename, Modifier.fillMaxWidth(), fill = true)
            TvButton("Delete", onDelete, Modifier.fillMaxWidth(), fill = true, style = "danger")
            TvButton("Cancel", onDismiss, Modifier.fillMaxWidth(), fill = true)
        }
    }
    LaunchedEffect(Unit) {
        delay(60)
        runCatching { first.requestFocus() }
    }
}

private val videoExt = setOf("mkv", "mp4", "m4v", "avi", "mov", "ts", "m2ts", "wmv", "webm")
private val imageExt = setOf("jpg", "jpeg", "png", "gif", "webp", "heic")
private val audioExt = setOf("mp3", "flac", "m4a", "aac", "wav", "ogg", "opus")
private val subExt = setOf("srt", "ass", "ssa", "sub", "idx", "vtt")
private val archiveExt = setOf("zip", "rar", "7z", "tar", "gz")

private fun fileIcon(e: FileEntry): String {
    if (e.isDir) return "📁"
    return when (e.name.substringAfterLast('.', "").lowercase()) {
        in videoExt -> "🎞️"
        in imageExt -> "🖼️"
        in audioExt -> "🎵"
        in subExt -> "💬"
        in archiveExt -> "🗜️"
        else -> "📄"
    }
}

private fun fmtSize(bytes: Long): String {
    var b = bytes.toDouble()
    val units = listOf("B", "KB", "MB", "GB", "TB")
    var i = 0
    while (b >= 1024 && i < units.lastIndex) {
        b /= 1024
        i++
    }
    return if (i <= 2) "%.0f %s".format(b, units[i]) else "%.1f %s".format(b, units[i])
}

private fun fmtDate(epochSeconds: Long): String =
    if (epochSeconds <= 0) "" else SimpleDateFormat("d MMM yyyy", Locale.getDefault()).format(Date(epochSeconds * 1000))

private val PlayableExt = setOf("mp4", "m4v", "mkv", "webm", "avi", "mov", "ts", "m2ts", "wmv", "mpg", "mpeg",
    "mp3", "m4a", "flac", "aac", "ogg", "opus", "wav")

internal fun isPlayable(name: String) = name.substringAfterLast('.', "").lowercase() in PlayableExt
