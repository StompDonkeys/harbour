package au.matt.harbour.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.ServiceInfo
import kotlinx.coroutines.delay

/**
 * One search for everything. Pick what you're after first — Movies, TV or Indexers — then type.
 * Switching afterwards re-runs the same words in the other place.
 */
@Composable
fun SearchScreen(hub: ServiceInfo, indexers: Boolean, onOpen: (String) -> Unit) {
    var query by rememberSaveable { mutableStateOf("") }
    var mode by rememberSaveable { mutableStateOf("") }   // "" (not chosen) | movie | tv | indexers
    var asking by rememberSaveable { mutableStateOf(false) }
    val firstChoice = remember { FocusRequester() }
    val searchBtn = remember { FocusRequester() }
    val sink = LocalBackdropSink.current
    if (mode != "movie" && mode != "tv") SideEffect { sink(null) }

    fun choose(m: String) {
        mode = m
        if (query.isBlank()) asking = true
    }

    val label = when (mode) {
        "movie" -> "movies"
        "tv" -> "TV shows"
        "indexers" -> "your indexers"
        else -> ""
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(start = 44.dp, end = 44.dp, top = 30.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(Modifier.weight(1f)) {
                Text("Search", fontSize = 26.sp, fontWeight = FontWeight.Bold)
                Text(
                    when {
                        mode.isEmpty() -> "What are you looking for?"
                        query.isBlank() -> "Search $label"
                        else -> "“$query” in $label"
                    },
                    color = HColors.TextDim, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            TvButton("Movies", { choose("movie") }, style = if (mode == "movie") "primary" else null,
                icon = HIcons.Movie, focusRequester = firstChoice)
            TvButton("TV", { choose("tv") }, style = if (mode == "tv") "primary" else null, icon = HIcons.Tv)
            if (indexers) {
                TvButton("Indexers", { choose("indexers") }, style = if (mode == "indexers") "primary" else null,
                    icon = HIcons.ManageSearch)
            }
            if (mode.isNotEmpty()) {
                Spacer(Modifier.width(6.dp))
                TvButton(if (query.isBlank()) "Search" else "New search", { asking = true },
                    icon = Icons.Filled.Search, focusRequester = searchBtn)
            }
        }
        Box(Modifier.weight(1f)) {
            when (mode) {
                "" -> Text(
                    "Choose Movies, TV" + (if (indexers) " or Indexers" else "") + " above, then type what you're after.",
                    color = HColors.TextDim, fontSize = 16.sp, modifier = Modifier.padding(start = 44.dp, top = 24.dp),
                )
                "indexers" -> IndexerScreen(externalQuery = query)
                else -> BrowseScreen(hub, searchFirst = false, onOpen = onOpen, externalQuery = query, onlyKind = mode)
            }
        }
    }

    // Land on the choices when the page opens fresh.
    LaunchedEffect(Unit) {
        if (mode.isEmpty()) {
            delay(80)
            runCatching { firstChoice.requestFocus() }
        }
    }

    if (asking) {
        TextInputDialog(
            title = "Search $label",
            initial = query,
            imeAction = ImeAction.Search,
            onDone = {
                asking = false
                if (it.isNotBlank()) query = it.trim()
            },
            onDismiss = { asking = false },
        )
    }
}
