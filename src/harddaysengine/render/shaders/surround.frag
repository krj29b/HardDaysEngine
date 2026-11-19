#version 330 core
// Clean-room widescreen surround composite shader (ARCHITECTURE.md §13.5, ADR-053).
// Stretches the heavily blurred canvas across widescreen side bars with darkening,
// gentle outer vignette, soft viewport-edge shadow, and sub-LSB gradient dithering.

uniform sampler2D u_blur_tex;
uniform vec2 u_blur_tex_size;
uniform vec2 u_win_size;
uniform vec4 u_dest_rect;
uniform float u_brightness;

in vec2 v_uv;
out vec4 f_color;

float interleaved_dither(vec2 frag_xy) {
    float n = fract(52.9829189 * fract(dot(frag_xy, vec2(0.06711056, 0.00583715))));
    return (n - 0.5) / 255.0;
}

void main() {
    if (u_brightness <= 0.0) {
        f_color = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    // 4-tap bilinear tent filter removes any trace of the low-res blur grid at ultrawide sizes
    vec2 half_px = vec2(0.75) / max(u_blur_tex_size, vec2(1.0));
    vec2 lo = vec2(0.5) / max(u_blur_tex_size, vec2(1.0));
    vec2 hi = vec2(1.0) - lo;
    vec3 col = (
        texture(u_blur_tex, clamp(v_uv + vec2(-half_px.x, -half_px.y), lo, hi)).rgb +
        texture(u_blur_tex, clamp(v_uv + vec2( half_px.x, -half_px.y), lo, hi)).rgb +
        texture(u_blur_tex, clamp(v_uv + vec2(-half_px.x,  half_px.y), lo, hi)).rgb +
        texture(u_blur_tex, clamp(v_uv + vec2( half_px.x,  half_px.y), lo, hi)).rgb
    ) * 0.25;

    col *= u_brightness;

    // Gentle outer edge vignette
    vec2 centered = v_uv * 2.0 - 1.0;
    vec2 vig_v = centered * vec2(0.95, 0.65);
    float vignette = mix(1.0, 0.72, clamp(dot(vig_v, vig_v), 0.0, 1.0));
    col *= vignette;

    // Soft dark drop-shadow immediately outside the game viewport so the game image pops cleanly
    vec2 dest_min = u_dest_rect.xy;
    vec2 dest_max = u_dest_rect.xy + u_dest_rect.zw;
    vec2 outside = max(max(dest_min - gl_FragCoord.xy, gl_FragCoord.xy - dest_max), vec2(0.0));
    float dist_px = length(outside);
    float shadow_width = max(12.0, u_win_size.y * 0.025);
    float border_shadow = mix(0.42, 1.0, smoothstep(0.0, shadow_width, dist_px));
    col *= border_shadow;

    if (max(col.r, max(col.g, col.b)) > 0.0005) {
        col = clamp(col + interleaved_dither(gl_FragCoord.xy), vec3(0.0), vec3(1.0));
    }
    f_color = vec4(col, 1.0);
}
