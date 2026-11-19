#version 330 core
// Clean-room xBR-style edge-directed 4x upscaler fragment shader (ARCHITECTURE.md §13.4.2, ADR-053).
// Evaluates symmetric 90-degree-rotated edge rules on a 21-texel 5x5 cross stencil in perceptual
// YUV space, with orthogonal-support anti-dither gating and 90-degree solid corner preservation.

uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;
uniform vec2 u_out_size;

in vec2 v_uv;
out vec4 f_color;

const float EQ_THRESH = 2.0;
const float EDGE_DOMINANCE = 1.15;
const float EDGE_MARGIN = 0.5;
const float INV_SQRT2 = 0.70710678;
const float INV_SQRT5 = 0.44721360;

vec3 to_yuv(vec3 rgb) {
    float y =  0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b;
    float u = -0.14713 * rgb.r - 0.28886 * rgb.g + 0.436 * rgb.b;
    float v =  0.615 * rgb.r - 0.51499 * rgb.g - 0.10001 * rgb.b;
    return vec3(y, u, v);
}

float df(vec3 a_yuv, vec3 b_yuv) {
    vec3 d = abs(a_yuv - b_yuv);
    return 48.0 * d.x + 7.0 * d.y + 6.0 * d.z;
}

bool eq_yuv(vec3 a_yuv, vec3 b_yuv) {
    return df(a_yuv, b_yuv) <= EQ_THRESH;
}

vec3 fetch_px(ivec2 base, ivec2 offset, ivec2 origin, ivec2 src_size, int tex_h) {
    ivec2 q = clamp(base + offset, ivec2(0), src_size - ivec2(1));
    ivec2 texel = ivec2(origin.x + q.x, tex_h - 1 - (origin.y + q.y));
    return texelFetch(u_input_tex, texel, 0).rgb;
}

// Evaluates one corner in canonical bottom-right orientation where (u, v) in [0, 1]^2 puts the
// active corner at (1, 1), +u points toward f, and +v points toward h.
// Returns vec4(target_rgb, blend_weight).
vec4 eval_corner(
    vec2 uv,
    float subpixel_width,
    vec3 e_rgb, vec3 f_rgb, vec3 h_rgb,
    vec3 e, vec3 f, vec3 h, vec3 i,
    vec3 b, vec3 d, vec3 a, vec3 c, vec3 g,
    vec3 f4, vec3 h5, vec3 i4, vec3 i5
) {
    float d_ef = df(e, f);
    float d_eh = df(e, h);
    if (d_ef <= EQ_THRESH || d_eh <= EQ_THRESH) {
        return vec4(e_rgb, 0.0);
    }

    // F and H must belong to the same side of the edge (rejects multi-colour dither triples)
    float d_fh = df(f, h);
    if (d_fh >= 0.5 * min(d_ef, d_eh)) {
        return vec4(e_rgb, 0.0);
    }

    // Anti-dither orthogonal-support gate: at least one side of the corner must connect
    // orthogonally to a matching neighbour (passes 1px thin diagonals, blocks checkerboards)
    bool has_support = eq_yuv(e, b) || eq_yuv(e, d) || eq_yuv(f, i) || eq_yuv(h, i);
    if (!has_support) {
        return vec4(e_rgb, 0.0);
    }

    // Preserve sharp 90-degree corners of >=2x2 axis-aligned solid blocks
    bool is_box_corner = eq_yuv(e, b) && eq_yuv(e, d) && eq_yuv(e, a)
                      && !eq_yuv(e, c) && !eq_yuv(e, g);
    if (is_box_corner) {
        return vec4(e_rgb, 0.0);
    }

    // Directional edge weights along F--H (w_e) vs perpendicular E--I (w_i)
    float w_e = df(e, c) + df(e, g) + df(i, h5) + df(i, f4) + 4.0 * d_fh;
    float w_i = df(h, d) + df(h, i5) + df(f, i4) + df(f, b) + 4.0 * df(e, i);
    if (w_e * EDGE_DOMINANCE + EDGE_MARGIN >= w_i) {
        return vec4(e_rgb, 0.0);
    }

    // Classify diagonal slope: 2:1 shallow, 1:2 steep, or 45-degree diagonal
    float left_metric = df(f, g);
    float up_metric = df(h, c);
    bool do_left = (2.0 * left_metric < up_metric) && !eq_yuv(e, g) && !eq_yuv(d, g);
    bool do_up   = (2.0 * up_metric < left_metric) && !eq_yuv(e, c) && !eq_yuv(b, c);

    float signed_dist;
    if (do_left) {
        signed_dist = (uv.x + 2.0 * uv.y - 2.0) * INV_SQRT5;
    } else if (do_up) {
        signed_dist = (2.0 * uv.x + uv.y - 2.0) * INV_SQRT5;
    } else {
        signed_dist = (uv.x + uv.y - 1.5) * INV_SQRT2;
    }

    float weight = clamp(signed_dist / subpixel_width + 0.5, 0.0, 1.0);
    vec3 target_rgb = (d_ef <= d_eh) ? f_rgb : h_rgb;
    return vec4(target_rgb, weight);
}

void main() {
    ivec2 origin = ivec2(round(u_in_origin));
    ivec2 src_size = ivec2(round(u_in_size));
    int tex_h = int(round(u_in_tex_size.y));

    vec2 src_pos = vec2(v_uv.x, 1.0 - v_uv.y) * u_in_size;
    ivec2 p0 = clamp(ivec2(floor(src_pos)), ivec2(0), src_size - ivec2(1));
    vec2 fp = clamp(src_pos - vec2(p0), vec2(0.0), vec2(1.0));
    float subpixel_width = max(u_in_size.x / max(u_out_size.x, 1.0), 0.125);

    // Fetch 21-texel 5x5 cross stencil in top-down source coordinates
    vec3 A1_rgb = fetch_px(p0, ivec2(-1, -2), origin, src_size, tex_h);
    vec3 B1_rgb = fetch_px(p0, ivec2( 0, -2), origin, src_size, tex_h);
    vec3 C1_rgb = fetch_px(p0, ivec2( 1, -2), origin, src_size, tex_h);

    vec3 A0_rgb = fetch_px(p0, ivec2(-2, -1), origin, src_size, tex_h);
    vec3 A_rgb  = fetch_px(p0, ivec2(-1, -1), origin, src_size, tex_h);
    vec3 B_rgb  = fetch_px(p0, ivec2( 0, -1), origin, src_size, tex_h);
    vec3 C_rgb  = fetch_px(p0, ivec2( 1, -1), origin, src_size, tex_h);
    vec3 C4_rgb = fetch_px(p0, ivec2( 2, -1), origin, src_size, tex_h);

    vec3 D0_rgb = fetch_px(p0, ivec2(-2,  0), origin, src_size, tex_h);
    vec3 D_rgb  = fetch_px(p0, ivec2(-1,  0), origin, src_size, tex_h);
    vec3 E_rgb  = fetch_px(p0, ivec2( 0,  0), origin, src_size, tex_h);
    vec3 F_rgb  = fetch_px(p0, ivec2( 1,  0), origin, src_size, tex_h);
    vec3 F4_rgb = fetch_px(p0, ivec2( 2,  0), origin, src_size, tex_h);

    vec3 G0_rgb = fetch_px(p0, ivec2(-2,  1), origin, src_size, tex_h);
    vec3 G_rgb  = fetch_px(p0, ivec2(-1,  1), origin, src_size, tex_h);
    vec3 H_rgb  = fetch_px(p0, ivec2( 0,  1), origin, src_size, tex_h);
    vec3 I_rgb  = fetch_px(p0, ivec2( 1,  1), origin, src_size, tex_h);
    vec3 I4_rgb = fetch_px(p0, ivec2( 2,  1), origin, src_size, tex_h);

    vec3 G5_rgb = fetch_px(p0, ivec2(-1,  2), origin, src_size, tex_h);
    vec3 H5_rgb = fetch_px(p0, ivec2( 0,  2), origin, src_size, tex_h);
    vec3 I5_rgb = fetch_px(p0, ivec2( 1,  2), origin, src_size, tex_h);

    // Precompute perceptual YUV once per stencil texel
    vec3 A1 = to_yuv(A1_rgb); vec3 B1 = to_yuv(B1_rgb); vec3 C1 = to_yuv(C1_rgb);
    vec3 A0 = to_yuv(A0_rgb); vec3 A  = to_yuv(A_rgb);  vec3 B  = to_yuv(B_rgb);
    vec3 C  = to_yuv(C_rgb);  vec3 C4 = to_yuv(C4_rgb);
    vec3 D0 = to_yuv(D0_rgb); vec3 D  = to_yuv(D_rgb);  vec3 E  = to_yuv(E_rgb);
    vec3 F  = to_yuv(F_rgb);  vec3 F4 = to_yuv(F4_rgb);
    vec3 G0 = to_yuv(G0_rgb); vec3 G  = to_yuv(G_rgb);  vec3 H  = to_yuv(H_rgb);
    vec3 I  = to_yuv(I_rgb);  vec3 I4 = to_yuv(I4_rgb);
    vec3 G5 = to_yuv(G5_rgb); vec3 H5 = to_yuv(H5_rgb); vec3 I5 = to_yuv(I5_rgb);

    // Evaluate all 4 corners via exact 90-degree rotations
    vec4 br = eval_corner(
        fp, subpixel_width,
        E_rgb, F_rgb, H_rgb,
        E, F, H, I, B, D, A, C, G, F4, H5, I4, I5
    );
    vec4 bl = eval_corner(
        vec2(fp.y, 1.0 - fp.x), subpixel_width,
        E_rgb, H_rgb, D_rgb,
        E, H, D, G, F, B, C, I, A, H5, D0, G5, G0
    );
    vec4 tl = eval_corner(
        vec2(1.0 - fp.x, 1.0 - fp.y), subpixel_width,
        E_rgb, D_rgb, B_rgb,
        E, D, B, A, H, F, I, G, C, D0, B1, A0, A1
    );
    vec4 tr = eval_corner(
        vec2(1.0 - fp.y, fp.x), subpixel_width,
        E_rgb, B_rgb, F_rgb,
        E, B, F, C, D, H, G, A, I, B1, F4, C1, C4
    );

    float w_total = br.a + bl.a + tl.a + tr.a;
    if (w_total <= 0.0) {
        f_color = vec4(E_rgb, 1.0);
        return;
    }

    vec3 blend_rgb = (br.rgb * br.a + bl.rgb * bl.a + tl.rgb * tl.a + tr.rgb * tr.a) / w_total;
    float alpha = min(w_total, 1.0);
    f_color = vec4(mix(E_rgb, blend_rgb, alpha), 1.0);
}
