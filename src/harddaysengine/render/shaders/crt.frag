#version 330 core
// Clean-room CRT presentation fragment shader (ARCHITECTURE.md §13.4.1, ADR-053).
// Models linear-light scanline beam widening, phosphor mask (aperture grille or shadow mask),
// optional barrel curvature with antialiased rounded bezel mask, and phosphor bloom.

uniform sampler2D u_canvas_tex;
uniform sampler2D u_bloom_tex;
uniform vec2 u_src_origin;
uniform vec2 u_src_size;
uniform vec2 u_tex_size;
uniform vec2 u_dest_size;
uniform int u_mask_type;
uniform float u_mask_strength;
uniform float u_scanline_strength;
uniform float u_curvature;
uniform float u_bloom_strength;

in vec2 v_uv;
out vec4 f_color;

vec2 warp_uv(vec2 uv, float curvature) {
    if (curvature <= 0.0) {
        return uv;
    }
    vec2 centered = uv * 2.0 - 1.0;
    vec2 sq = centered.yx * centered.yx;
    centered = centered + centered * sq * curvature;
    return centered * 0.5 + 0.5;
}

float bezel_mask(vec2 w_uv, float curvature, vec2 dest_size) {
    if (curvature <= 0.0) {
        return 1.0;
    }
    float aspect = dest_size.x / max(dest_size.y, 1.0);
    vec2 half_ext = vec2(0.5 * aspect, 0.5);
    vec2 p = abs((w_uv - 0.5) * vec2(aspect, 1.0));
    float radius = clamp(curvature * 0.45, 0.015, 0.06);
    vec2 q = p - (half_ext - vec2(radius));
    float dist = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - radius;
    float feather = 1.5 / max(dest_size.y, 1.0);
    return 1.0 - smoothstep(-feather, feather, dist);
}

vec3 sample_row_linear(float sx, float row_center) {
    float prescale_x = max(1.0, floor(u_dest_size.x / max(u_src_size.x, 1.0)));
    float rel = sx - 0.5;
    float base = floor(rel);
    float f = clamp((rel - base - 0.5) * prescale_x + 0.5, 0.0, 1.0);
    float x_coord = clamp(base + 0.5 + f, 0.5, u_src_size.x - 0.5);
    float y_coord = clamp(row_center, 0.5, u_src_size.y - 0.5);
    vec2 uv = vec2(
        (u_src_origin.x + x_coord) / u_tex_size.x,
        1.0 - (u_src_origin.y + y_coord) / u_tex_size.y
    );
    vec3 srgb = texture(u_canvas_tex, uv).rgb;
    return pow(max(srgb, vec3(0.0)), vec3(2.2));
}

vec3 scanline_beam(float dist, vec3 lin_col, float strength) {
    vec3 beam_k = mix(vec3(16.0), vec3(7.5), sqrt(clamp(lin_col, vec3(0.0), vec3(1.0))));
    vec3 gauss = exp(-beam_k * dist * dist);
    float linear_w = clamp(1.0 - dist, 0.0, 1.0);
    return mix(vec3(linear_w), gauss, strength);
}

vec3 phosphor_mask(ivec2 out_px, int mask_type, float strength) {
    if (mask_type == 0 || strength <= 0.0) {
        return vec3(1.0);
    }
    float dim = 1.0 - strength;
    if (mask_type == 1) {
        // Aperture grille: repeating 3-pixel vertical RGB phosphor stripes
        int sub = out_px.x % 3;
        vec3 triad = (sub == 0) ? vec3(1.0, dim, dim)
                   : (sub == 1) ? vec3(dim, 1.0, dim)
                                : vec3(dim, dim, 1.0);
        float gain = 1.0 / (1.0 - 0.5 * strength);
        return triad * gain;
    }
    // Shadow / slot mask: staggered 3-column x 2-row RGB phosphor triads with subtle slot bridge
    int phase = (out_px.y >> 1) & 1;
    int sub = (out_px.x + phase) % 3;
    vec3 triad = (sub == 0) ? vec3(1.0, dim, dim)
               : (sub == 1) ? vec3(dim, 1.0, dim)
                            : vec3(dim, dim, 1.0);
    float slot = ((out_px.y & 1) == 0) ? 1.0 : (1.0 - 0.35 * strength);
    float gain = 1.0 / (1.0 - 0.55 * strength);
    return triad * slot * gain;
}

void main() {
    vec2 top_down_uv = vec2(v_uv.x, 1.0 - v_uv.y);
    vec2 w_uv = warp_uv(top_down_uv, u_curvature);
    if (w_uv.x < 0.0 || w_uv.x > 1.0 || w_uv.y < 0.0 || w_uv.y > 1.0) {
        f_color = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    float mask_edge = bezel_mask(w_uv, u_curvature, u_dest_size);
    if (mask_edge <= 0.0) {
        f_color = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    // Two-row intensity-dependent Gaussian scanline beam reconstruction in linear light
    vec2 src_pos = w_uv * u_src_size;
    float y_rel = src_pos.y - 0.5;
    float row0 = floor(y_rel);
    float t = y_rel - row0;
    float d0 = t;
    float d1 = 1.0 - t;

    vec3 col0 = sample_row_linear(src_pos.x, row0 + 0.5);
    vec3 col1 = sample_row_linear(src_pos.x, row0 + 1.5);
    vec3 w0 = scanline_beam(d0, col0, u_scanline_strength);
    vec3 w1 = scanline_beam(d1, col1, u_scanline_strength);
    vec3 color = (col0 * w0 + col1 * w1) * (1.0 + 0.18 * u_scanline_strength);

    // Optical phosphor bloom / halation
    if (u_bloom_strength > 0.0) {
        vec2 half_texel = vec2(0.5) / max(u_src_size, vec2(1.0));
        vec2 b_clamped = clamp(w_uv, half_texel, vec2(1.0) - half_texel);
        vec2 b_uv = vec2(b_clamped.x, 1.0 - b_clamped.y);
        vec3 b_enc = texture(u_bloom_tex, b_uv).rgb;
        color += (b_enc * b_enc) * u_bloom_strength;
    }

    // Output-pixel phosphor mask
    ivec2 out_px = ivec2(floor(gl_FragCoord.xy));
    color *= phosphor_mask(out_px, u_mask_type, u_mask_strength);

    // Subtle glass edge vignette when curvature is enabled
    if (u_curvature > 0.0) {
        vec2 vig_d = w_uv * (1.0 - w_uv) * 4.0;
        float vig = pow(clamp(vig_d.x * vig_d.y, 0.0, 1.0), u_curvature * 0.6);
        color *= vig;
    }

    color *= mask_edge;
    vec3 out_srgb = pow(clamp(color, vec3(0.0), vec3(1.0)), vec3(1.0 / 2.2));
    f_color = vec4(out_srgb, 1.0);
}
