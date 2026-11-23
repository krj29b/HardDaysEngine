#version 330 core
// Single-pass 5x5 edge-directed smoothing over 1x R8 indexed textures (ARCHITECTURE.md §14.6.1).
// Respects palette index 0 transparency without dark-fringe blending artefacts.

uniform sampler2D u_index_tex;
uniform sampler2D u_live_palette;
uniform vec2 u_tex_size;
uniform int u_scale;
uniform int u_transparent_zero;

in vec2 v_uv;
out vec4 f_color;

const float EQ_THRESH = 2.0;
const float EDGE_DOMINANCE = 1.15;
const float EDGE_MARGIN = 0.5;
const float INV_SQRT2 = 0.70710678;
const float INV_SQRT5 = 0.44721360;
const float ALPHA_MISMATCH_PENALTY = 200.0;

vec3 to_yuv(vec3 rgb) {
    float y =  0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b;
    float u = -0.14713 * rgb.r - 0.28886 * rgb.g + 0.436 * rgb.b;
    float v =  0.615 * rgb.r - 0.51499 * rgb.g - 0.10001 * rgb.b;
    return vec3(y, u, v);
}

float df_rgba(vec4 a_yuva, vec4 b_yuva) {
    bool a_trans = a_yuva.a < 0.5;
    bool b_trans = b_yuva.a < 0.5;
    if (a_trans && b_trans) {
        return 0.0;
    }
    if (a_trans != b_trans) {
        return ALPHA_MISMATCH_PENALTY;
    }
    vec3 d = abs(a_yuva.xyz - b_yuva.xyz);
    return 48.0 * d.x + 7.0 * d.y + 6.0 * d.z;
}

bool eq_yuva(vec4 a_yuva, vec4 b_yuva) {
    return df_rgba(a_yuva, b_yuva) <= EQ_THRESH;
}

vec4 fetch_px(ivec2 base, ivec2 offset, ivec2 tex_size) {
    ivec2 q = base + offset;
    if (q.x < 0 || q.y < 0 || q.x >= tex_size.x || q.y >= tex_size.y) {
        if (u_transparent_zero == 1) {
            return vec4(0.0, 0.0, 0.0, 0.0);
        }
        q = clamp(q, ivec2(0), tex_size - ivec2(1));
    }
    int idx = int(texelFetch(u_index_tex, q, 0).r * 255.0 + 0.5) & 31;
    if (u_transparent_zero == 1 && idx == 0) {
        return vec4(0.0, 0.0, 0.0, 0.0);
    }
    vec3 rgb = texelFetch(u_live_palette, ivec2(idx, 0), 0).rgb;
    return vec4(rgb, 1.0);
}

vec4 to_yuva(vec4 rgba) {
    return vec4(to_yuv(rgba.rgb), rgba.a);
}

// Evaluates one corner in canonical bottom-right orientation where (u, v) in [0, 1]^2 puts the
// active corner at (1, 1), +u points toward f, and +v points toward h.
// Returns vec4(corner_rgb, corner_alpha) via target_out and blend weight as return value.
float eval_corner(
    vec2 uv,
    float subpixel_width,
    vec4 e_rgba, vec4 f_rgba, vec4 h_rgba,
    vec4 e, vec4 f, vec4 h, vec4 i,
    vec4 b, vec4 d, vec4 a, vec4 c, vec4 g,
    vec4 f4, vec4 h5, vec4 i4, vec4 i5,
    out vec4 target_out
) {
    target_out = e_rgba;
    float d_ef = df_rgba(e, f);
    float d_eh = df_rgba(e, h);
    if (d_ef <= EQ_THRESH || d_eh <= EQ_THRESH) {
        return 0.0;
    }

    float d_fh = df_rgba(f, h);
    if (d_fh >= 0.5 * min(d_ef, d_eh)) {
        return 0.0;
    }

    bool has_support = eq_yuva(e, b) || eq_yuva(e, d) || eq_yuva(f, i) || eq_yuva(h, i);
    if (!has_support) {
        return 0.0;
    }

    bool is_box_corner = eq_yuva(e, b) && eq_yuva(e, d) && eq_yuva(e, a)
                      && !eq_yuva(e, c) && !eq_yuva(e, g);
    if (is_box_corner) {
        return 0.0;
    }

    float w_e = df_rgba(e, c) + df_rgba(e, g) + df_rgba(i, h5) + df_rgba(i, f4) + 4.0 * d_fh;
    float w_i = df_rgba(h, d) + df_rgba(h, i5) + df_rgba(f, i4) + df_rgba(f, b) + 4.0 * df_rgba(e, i);
    if (w_e * EDGE_DOMINANCE + EDGE_MARGIN >= w_i) {
        return 0.0;
    }

    float left_metric = df_rgba(f, g);
    float up_metric = df_rgba(h, c);
    bool do_left = (2.0 * left_metric < up_metric) && !eq_yuva(e, g) && !eq_yuva(d, g);
    bool do_up   = (2.0 * up_metric < left_metric) && !eq_yuva(e, c) && !eq_yuva(b, c);

    float signed_dist;
    if (do_left) {
        signed_dist = (uv.x + 2.0 * uv.y - 2.0) * INV_SQRT5;
    } else if (do_up) {
        signed_dist = (2.0 * uv.x + uv.y - 2.0) * INV_SQRT5;
    } else {
        signed_dist = (uv.x + uv.y - 1.5) * INV_SQRT2;
    }

    float weight = clamp(signed_dist / subpixel_width + 0.5, 0.0, 1.0);
    vec4 picked = (d_ef <= d_eh) ? f_rgba : h_rgba;
    if (e_rgba.a > 0.5 && picked.a < 0.5) {
        target_out = vec4(e_rgba.rgb, 0.0);
    } else {
        target_out = picked;
    }
    return weight;
}

void main() {
    ivec2 tex_size = ivec2(round(u_tex_size));
    vec2 src_pos = v_uv * u_tex_size;
    ivec2 p0 = clamp(ivec2(floor(src_pos)), ivec2(0), tex_size - ivec2(1));
    vec2 fp = clamp(src_pos - vec2(p0), vec2(0.0), vec2(1.0));
    float subpixel_width = max(1.0 / max(float(u_scale), 1.0), 0.125);

    // Fetch 21-texel 5x5 cross stencil directly in uploaded top-down R8 texture coordinates
    vec4 A1_rgba = fetch_px(p0, ivec2(-1, -2), tex_size);
    vec4 B1_rgba = fetch_px(p0, ivec2( 0, -2), tex_size);
    vec4 C1_rgba = fetch_px(p0, ivec2( 1, -2), tex_size);

    vec4 A0_rgba = fetch_px(p0, ivec2(-2, -1), tex_size);
    vec4 A_rgba  = fetch_px(p0, ivec2(-1, -1), tex_size);
    vec4 B_rgba  = fetch_px(p0, ivec2( 0, -1), tex_size);
    vec4 C_rgba  = fetch_px(p0, ivec2( 1, -1), tex_size);
    vec4 C4_rgba = fetch_px(p0, ivec2( 2, -1), tex_size);

    vec4 D0_rgba = fetch_px(p0, ivec2(-2,  0), tex_size);
    vec4 D_rgba  = fetch_px(p0, ivec2(-1,  0), tex_size);
    vec4 E_rgba  = fetch_px(p0, ivec2( 0,  0), tex_size);
    vec4 F_rgba  = fetch_px(p0, ivec2( 1,  0), tex_size);
    vec4 F4_rgba = fetch_px(p0, ivec2( 2,  0), tex_size);

    vec4 G0_rgba = fetch_px(p0, ivec2(-2,  1), tex_size);
    vec4 G_rgba  = fetch_px(p0, ivec2(-1,  1), tex_size);
    vec4 H_rgba  = fetch_px(p0, ivec2( 0,  1), tex_size);
    vec4 I_rgba  = fetch_px(p0, ivec2( 1,  1), tex_size);
    vec4 I4_rgba = fetch_px(p0, ivec2( 2,  1), tex_size);

    vec4 G5_rgba = fetch_px(p0, ivec2(-1,  2), tex_size);
    vec4 H5_rgba = fetch_px(p0, ivec2( 0,  2), tex_size);
    vec4 I5_rgba = fetch_px(p0, ivec2( 1,  2), tex_size);

    vec4 A1 = to_yuva(A1_rgba); vec4 B1 = to_yuva(B1_rgba); vec4 C1 = to_yuva(C1_rgba);
    vec4 A0 = to_yuva(A0_rgba); vec4 A  = to_yuva(A_rgba);  vec4 B  = to_yuva(B_rgba);
    vec4 C  = to_yuva(C_rgba);  vec4 C4 = to_yuva(C4_rgba);
    vec4 D0 = to_yuva(D0_rgba); vec4 D  = to_yuva(D_rgba);  vec4 E  = to_yuva(E_rgba);
    vec4 F  = to_yuva(F_rgba);  vec4 F4 = to_yuva(F4_rgba);
    vec4 G0 = to_yuva(G0_rgba); vec4 G  = to_yuva(G_rgba);  vec4 H  = to_yuva(H_rgba);
    vec4 I  = to_yuva(I_rgba);  vec4 I4 = to_yuva(I4_rgba);
    vec4 G5 = to_yuva(G5_rgba); vec4 H5 = to_yuva(H5_rgba); vec4 I5 = to_yuva(I5_rgba);

    vec4 br_t; vec4 bl_t; vec4 tl_t; vec4 tr_t;
    float w_br = eval_corner(
        fp, subpixel_width,
        E_rgba, F_rgba, H_rgba,
        E, F, H, I, B, D, A, C, G, F4, H5, I4, I5,
        br_t
    );
    float w_bl = eval_corner(
        vec2(fp.y, 1.0 - fp.x), subpixel_width,
        E_rgba, H_rgba, D_rgba,
        E, H, D, G, F, B, C, I, A, H5, D0, G5, G0,
        bl_t
    );
    float w_tl = eval_corner(
        vec2(1.0 - fp.x, 1.0 - fp.y), subpixel_width,
        E_rgba, D_rgba, B_rgba,
        E, D, B, A, H, F, I, G, C, D0, B1, A0, A1,
        tl_t
    );
    float w_tr = eval_corner(
        vec2(1.0 - fp.y, fp.x), subpixel_width,
        E_rgba, B_rgba, F_rgba,
        E, B, F, C, D, H, G, A, I, B1, F4, C1, C4,
        tr_t
    );

    float w_total = w_br + w_bl + w_tl + w_tr;
    if (w_total <= 0.0) {
        if (E_rgba.a <= 0.001) {
            discard;
        }
        f_color = E_rgba;
        return;
    }

    vec4 blend = (br_t * w_br + bl_t * w_bl + tl_t * w_tl + tr_t * w_tr) / w_total;
    float alpha = min(w_total, 1.0);
    vec3 out_rgb = (E_rgba.a < 0.5) ? blend.rgb : mix(E_rgba.rgb, blend.rgb, alpha);
    float out_a = mix(E_rgba.a, blend.a, alpha);
    if (out_a <= 0.001) {
        discard;
    }
    f_color = vec4(out_rgb, out_a);
}
