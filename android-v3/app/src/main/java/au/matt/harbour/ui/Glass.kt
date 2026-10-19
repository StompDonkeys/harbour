package au.matt.harbour.ui

import android.graphics.Bitmap
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import coil.size.Size
import coil.transform.Transformation
import kotlin.math.roundToInt

/**
 * The full-screen artwork currently behind the UI (already gateway-resolved URL).
 * Glass panels draw a blurred copy of it, so they look frosted even though Fire OS
 * can't blur live content.
 */
val LocalBackdrop = compositionLocalOf<String?> { null }

/** Screens report the artwork they're showing here, so the shell can hand it to glass panels. */
val LocalBackdropSink = compositionLocalOf<(String?) -> Unit> { {} }

/** Cheap, good-looking blur: shrink the image, box-blur it three times, let it scale back up smoothly. */
class BlurTransformation(private val radius: Int = 6, private val sampleWidth: Int = 192) : Transformation {
    override val cacheKey: String = "harbour-blur-$radius-$sampleWidth"

    override suspend fun transform(input: Bitmap, size: Size): Bitmap {
        val w = sampleWidth.coerceAtMost(input.width).coerceAtLeast(8)
        val h = (input.height.toFloat() * w / input.width).roundToInt().coerceAtLeast(8)
        val small = Bitmap.createScaledBitmap(input, w, h, true).copy(Bitmap.Config.ARGB_8888, true)
        val px = IntArray(w * h)
        small.getPixels(px, 0, w, 0, 0, w, h)
        repeat(3) {
            boxBlur(px, w, h, radius, horizontal = true)
            boxBlur(px, w, h, radius, horizontal = false)
        }
        small.setPixels(px, 0, w, 0, 0, w, h)
        return small
    }

    private fun boxBlur(px: IntArray, w: Int, h: Int, r: Int, horizontal: Boolean) {
        val lines = if (horizontal) h else w
        val len = if (horizontal) w else h
        val out = IntArray(len)
        for (line in 0 until lines) {
            fun idx(i: Int) = if (horizontal) line * w + i else i * w + line
            var a = 0; var rr = 0; var g = 0; var b = 0
            for (k in -r..r) {
                val c = px[idx(k.coerceIn(0, len - 1))]
                a += c ushr 24; rr += (c shr 16) and 255; g += (c shr 8) and 255; b += c and 255
            }
            val n = 2 * r + 1
            for (i in 0 until len) {
                out[i] = ((a / n) shl 24) or ((rr / n) shl 16) or ((g / n) shl 8) or (b / n)
                val add = px[idx((i + r + 1).coerceAtMost(len - 1))]
                val sub = px[idx((i - r).coerceAtLeast(0))]
                a += (add ushr 24) - (sub ushr 24)
                rr += ((add shr 16) and 255) - ((sub shr 16) and 255)
                g += ((add shr 8) and 255) - ((sub shr 8) and 255)
                b += (add and 255) - (sub and 255)
            }
            for (i in 0 until len) px[idx(i)] = out[i]
        }
    }
}

/**
 * A frosted-glass surface. Draws the blurred backdrop lined up with the real one,
 * then a dark tint, a hairline edge and the content.
 */
@Composable
fun GlassPanel(
    modifier: Modifier = Modifier,
    shape: Shape = RoundedCornerShape(22.dp),
    tint: Color = HColors.GlassDark,
    edge: Color = HColors.GlassEdge,
    content: @Composable BoxScope.() -> Unit,
) {
    val backdrop = LocalBackdrop.current
    val context = LocalContext.current
    val config = LocalConfiguration.current
    var origin by remember { mutableStateOf(Offset.Zero) }
    Box(
        modifier
            .onGloballyPositioned { origin = it.positionInRoot() }
            .clip(shape)
            .border(1.dp, edge, shape),
    ) {
        if (backdrop != null) {
            val req = remember(backdrop) {
                ImageRequest.Builder(context).data(backdrop).size(640, 360)
                    .transformations(BlurTransformation()).crossfade(true).build()
            }
            // matchParentSize: the blurred art fills the panel but never decides its size.
            Box(Modifier.matchParentSize()) {
                AsyncImage(
                    model = req,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .wrapContentSize(Alignment.TopStart, unbounded = true)
                        .offset { IntOffset(-origin.x.roundToInt(), -origin.y.roundToInt()) }
                        .requiredSize(config.screenWidthDp.dp, config.screenHeightDp.dp),
                )
            }
        }
        Box(Modifier.matchParentSize().background(tint))
        content()
    }
}

/** Glass without the art behind it (dialogs, menus): a solid frosted-dark surface. */
@Composable
fun FrostPanel(
    modifier: Modifier = Modifier,
    shape: Shape = RoundedCornerShape(26.dp),
    content: @Composable BoxScope.() -> Unit,
) {
    Box(
        modifier
            .clip(shape)
            .background(Color(0xF21C1C21))
            .border(1.dp, HColors.GlassEdge, shape),
        content = content,
    )
}
