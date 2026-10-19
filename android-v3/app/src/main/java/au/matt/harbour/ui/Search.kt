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
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.ServiceInfo

/**
 * One search for everything: type once, then flip between titles (movies & TV to add or open)
 * and raw indexer releases (send straight to NZBGet / qBittorrent).
 */
@Composable
fun SearchScreen(hub: ServiceInfo, indexers: Boolean, onOpen: (String) -> Unit) {
    var query by rememberSaveable { mutableStateOf("") }
    var mode by rememberSaveable { mutableStateOf("titles") }   // titles | indexers
    var asking by rememberSaveable { mutableStateOf(true) }
    val sink = LocalBackdropSink.current
    if (mode == "indexers") SideEffect { sink(null) }

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
                        query.isBlank() -> if (indexers) "Movies & TV, or your indexers directly" else "Find any movie or show"
                        mode == "indexers" -> "“$query” on your indexers"
                        else -> "“$query” in movies & TV"
                    },
                    color = HColors.TextDim, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            if (indexers) {
                FilterChip("Movies & TV", mode == "titles") { mode = "titles" }
                FilterChip("Indexers", mode == "indexers") { mode = "indexers" }
                Spacer(Modifier.width(6.dp))
            }
            TvButton(if (query.isBlank()) "Search" else "New search", { asking = true },
                style = "primary", icon = Icons.Filled.Search)
        }
        Box(Modifier.weight(1f)) {
            if (mode == "indexers" && indexers) IndexerScreen(externalQuery = query)
            else BrowseScreen(hub, searchFirst = false, onOpen = onOpen, externalQuery = query)
        }
    }

    if (asking) {
        TextInputDialog(
            title = if (mode == "indexers") "Search indexers" else "Search movies & TV",
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
