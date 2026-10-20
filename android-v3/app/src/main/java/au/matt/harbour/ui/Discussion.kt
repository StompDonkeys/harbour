package au.matt.harbour.ui

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
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.Comment
import au.matt.harbour.data.Discussion
import au.matt.harbour.data.discussion
import au.matt.harbour.data.discussionReplies
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Opens the Discussion page for a gateway "discuss" action: (ref, title). */
val LocalOpenDiscussion = compositionLocalOf<(String, String) -> Unit> { { _, _ -> } }

/**
 * TV Time-style conversation from Trakt about a movie, a show or one episode.
 * OK on a spoiler shows it; OK on a comment with replies opens them underneath.
 */
@Composable
fun DiscussionScreen(ref: String, title: String) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val scope = rememberCoroutineScope()
    var sort by rememberSaveable { mutableStateOf("likes") }
    var data by remember { mutableStateOf<Discussion?>(null) }
    var comments by remember { mutableStateOf<List<Comment>>(emptyList()) }
    var err by remember { mutableStateOf<String?>(null) }
    var retry by remember { mutableIntStateOf(0) }
    var loadingMore by remember { mutableStateOf(false) }
    val revealed = remember { mutableStateMapOf<Long, Boolean>() }
    val replies = remember { mutableStateMapOf<Long, List<Comment>>() }
    val firstTab = remember { FocusRequester() }

    LaunchedEffect(sort, retry) {
        data = null
        err = null
        comments = emptyList()
        try {
            val d = gw.discussion(ref, sort, 1)
            data = d
            comments = d.comments
        } catch (e: Exception) {
            err = e.message
        }
    }
    LaunchedEffect(Unit) {
        delay(80)
        runCatching { firstTab.requestFocus() }
    }

    fun loadMore() {
        val d = data ?: return
        if (loadingMore || d.page >= d.pages) return
        loadingMore = true
        scope.launch {
            try {
                val n = gw.discussion(ref, sort, d.page + 1)
                data = n
                comments = comments + n.comments
            } catch (e: Exception) {
                toaster.show("⚠ ${e.message}")
            } finally {
                loadingMore = false
            }
        }
    }

    fun toggleReplies(c: Comment) {
        if (replies.containsKey(c.id)) {
            replies.remove(c.id)
            return
        }
        scope.launch {
            try {
                replies[c.id] = gw.discussionReplies(c.id).comments
            } catch (e: Exception) {
                toaster.show("⚠ ${e.message}")
            }
        }
    }

    Column(Modifier.fillMaxSize().padding(start = 40.dp, end = 36.dp, top = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(data?.title?.ifBlank { null } ?: title, fontSize = 26.sp, fontWeight = FontWeight.Bold,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                val d = data
                Text(
                    listOfNotNull(
                        d?.episodeTitle,
                        d?.rating?.let { "$it% on Trakt · ${d.votes} votes" },
                        d?.let { "${it.total} comment${if (it.total == 1) "" else "s"}" },
                    ).joinToString("  ·  ").ifBlank { "Discussion from Trakt" },
                    color = HColors.TextDim, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                TvButton("Most liked", { sort = "likes" }, style = if (sort == "likes") "primary" else null,
                    focusRequester = firstTab)
                TvButton("Newest", { sort = "newest" }, style = if (sort == "newest") "primary" else null)
                TvButton("Most replies", { sort = "replies" }, style = if (sort == "replies") "primary" else null)
            }
        }
        Spacer(Modifier.height(14.dp))
        Box(Modifier.weight(1f)) {
            val d = data
            when {
                err != null -> ErrorPanel(err ?: "", onRetry = { retry++ })
                d == null -> Loading()
                comments.isEmpty() -> Text("No comments yet.", color = HColors.TextDim, fontSize = 16.sp)
                else -> LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding = PaddingValues(start = 4.dp, end = 4.dp, top = 4.dp, bottom = 60.dp),
                ) {
                    itemsIndexed(comments, key = { i, c -> "$i:${c.id}" }) { i, c ->
                        CommentCard(
                            c = c,
                            showSpoiler = revealed[c.id] == true,
                            replies = replies[c.id],
                            onClick = {
                                when {
                                    c.spoiler && revealed[c.id] != true -> revealed[c.id] = true
                                    c.replies > 0 -> toggleReplies(c)
                                }
                            },
                        )
                        if (i >= comments.size - 3) LaunchedEffect(comments.size) { loadMore() }
                    }
                }
            }
        }
    }
}

@Composable
private fun CommentCard(c: Comment, showSpoiler: Boolean, replies: List<Comment>?, onClick: () -> Unit) {
    FocusBox(onClick = onClick, modifier = Modifier.fillMaxWidth(), focusedScale = 1.01f) { f ->
        Column(
            Modifier.fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.7f))
                .padding(horizontal = 18.dp, vertical = 12.dp),
        ) {
            CommentHead(c)
            Spacer(Modifier.height(4.dp))
            if (c.spoiler && !showSpoiler) {
                Text("⚠  Spoiler — press OK to read", fontSize = 15.sp, color = HColors.TextDim,
                    modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(HColors.GlassFill)
                        .padding(horizontal = 12.dp, vertical = 8.dp))
            } else {
                Text(c.text, fontSize = 16.sp, lineHeight = 22.sp, color = HColors.Text,
                    maxLines = if (f) 30 else 6, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(6.dp))
            Text(
                listOfNotNull(
                    "♥ ${c.likes}",
                    if (c.replies > 0) "${c.replies} repl${if (c.replies == 1) "y" else "ies"}" +
                        (if (replies == null) " — OK to open" else "") else null,
                    if (c.review) "Review" else null,
                ).joinToString("   "),
                fontSize = 13.sp, color = HColors.TextDim,
            )
            if (replies != null) {
                Column(
                    Modifier.padding(top = 10.dp, start = 6.dp)
                        .widthIn(max = 1400.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    replies.forEach { r ->
                        Row {
                            Box(Modifier.width(3.dp).height(40.dp).background(HColors.GlassEdge))
                            Spacer(Modifier.width(12.dp))
                            Column {
                                CommentHead(r)
                                Text(if (r.spoiler) "⚠ Spoiler hidden" else r.text, fontSize = 15.sp,
                                    lineHeight = 20.sp, color = if (r.spoiler) HColors.TextDim else HColors.Text)
                            }
                        }
                    }
                    if (replies.isEmpty()) Text("No replies.", fontSize = 13.sp, color = HColors.TextDim)
                }
            }
        }
    }
}

@Composable
private fun CommentHead(c: Comment) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(c.user, fontSize = 15.sp, fontWeight = FontWeight.Bold)
        c.rating?.let {
            Spacer(Modifier.width(8.dp))
            Text("★ $it/10", fontSize = 13.sp, color = HColors.Warn)
        }
        Spacer(Modifier.weight(1f))
        c.created?.let { Text(whenAgo(it), fontSize = 12.sp, color = HColors.TextFaint) }
    }
}

private fun whenAgo(iso: String): String {
    val t = runCatching {
        java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", java.util.Locale.US)
            .apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }
            .parse(iso.take(19))!!.time / 1000
    }.getOrNull() ?: return ""
    val s = System.currentTimeMillis() / 1000 - t
    return when {
        s < 90 -> "just now"
        s < 3600 -> "${s / 60} min ago"
        s < 86400 -> "${s / 3600} h ago"
        s < 30 * 86400 -> "${s / 86400} d ago"
        else -> java.text.SimpleDateFormat("d MMM yyyy", java.util.Locale.getDefault()).format(java.util.Date(t * 1000))
    }
}
